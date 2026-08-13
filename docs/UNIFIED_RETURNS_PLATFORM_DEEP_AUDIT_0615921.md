# Deep Repository Audit — Unified Returns Platform

**Repository:** `uvelan/returns_muti_agentic_platform`
**Branch:** `refactor/unified-return-platform`
**Commit audited:** `061592121325af08765f000029faa559d4423210`
**Commit timestamp:** 2026-08-13 17:05:28 +0530
**Local vs remote:** identical (`git rev-parse HEAD` == `origin/refactor/unified-return-platform`), working tree clean
**Audit date:** 2026-08-13
**Audit type:** Read-only. No production code was modified.

---

## Baseline: Stack Inventory

| Layer | Technology | Evidence |
|---|---|---|
| Backend | Python, FastAPI, Pydantic v2 | `backend/pyproject.toml`, `backend/src/return_platform/main.py` |
| Backend size | 524 `.py` files, ~89,800 lines | measured |
| Workflow engine | Temporal (`temporalio`) | `backend/src/return_platform/workflows/worker.py` |
| Graph database | Neo4j (async driver) | `backend/scripts/run_order_discovery_worker.py:42` |
| Platform store | MongoDB (`AsyncMongoClient`) | same, line 29 |
| Source stores | MongoDB (source), SQL Server (`pymssql`), PostgreSQL | `backend/src/return_platform/source_connectors/` |
| Cache/streams | Valkey | `compose.yaml` |
| Secrets | HashiCorp Vault | `backend/src/return_platform/secrets/runtime.py` |
| AI providers | Gemini, NVIDIA, OpenAI, Anthropic, Ollama, Simulator, Manual | `backend/src/return_platform/ai/providers/registry.py:13` |
| Frontend | React + TypeScript + Vite + Tailwind, Vitest, Playwright | `frontend/package.json` |
| Frontend size | 67 `.ts/.tsx` files, ~23,600 lines | measured |
| API surface | 126 documented paths, OpenAPI 3.1.0 | `openapi.json` |
| Runtime services | API, Order Discovery worker, Return workflow worker, integration outbox worker, interception resume worker | `backend/scripts/`, `backend/src/return_platform/workers/` |

**Verification performed live during this audit:**

| Check | Result |
|---|---|
| `npm run test` (frontend Vitest) | **18 files, 168 tests, all passed** |
| `npm run typecheck` (`tsc -b`) | **exit 0, clean** |
| `pytest --collect-only` (backend) | **2,793 tests collected, no collection errors** |
| `pytest tests/test_frozen_modules_gain_no_new_callers.py tests/dynamic_knowledge/test_search_strategy.py` | **43 passed** |
| Full backend suite (2,793 tests) | **Aborted.** Run terminated at a 120-second per-test timeout inside `tests/test_order_agent_rest.py::test_order_agent_scenarios`, which blocks on `client.post(...)` awaiting live infrastructure. No pass/fail signal for the suite as a whole. Marked `NOT PROVABLE` where relied upon. |

---

# Section A — Executive Verdict

## Overall status: **NOT RELEASABLE**

The repository is, in most respects, unusually well-engineered. Redaction, pricing, tenant scoping, the approval kernel, the analyzer's package independence, candidate-set binding and confirmation idempotency are all implemented to a genuinely high standard, with dense and honest inline documentation. The frontend is clean and fully typechecked with a green test suite.

But the audit found one defect that invalidates the product's central claim: **the durable case workflow that drives everything after order confirmation is never started by any production code path.** Steps 12 through 22 of the required business flow — concurrent Bay Assignment, the support conversation, durable reminders, RMA creation, and propagation of the RMA back into the associate's conversation — are unreachable in a deployed system. `ReturnCaseWorkflow.run` is launched in exactly one place in the entire repository: a test file.

A second, independent P0 exists in Order Discovery: the misspelling fallback issues an **unfiltered, unordered, 100-row read** of the customer table and scores it client-side with `difflib`. At production customer volumes this will silently fail to find the correct order.

A recurring structural pattern compounds both: **the frozen legacy implementation is repeatedly more capable than the canonical replacement.** The deprecated `associate_flow.py` uses a real Neo4j full-text index for fuzzy search; the canonical agent uses the bounded `difflib` probe. The legacy `AIGatewayService.evaluate` supports pre-dispatch human interception; the canonical `structured_invocation` path used by both the Order Agent and the Graph Analyzer does not. Consolidation moved traffic to the new path before the new path reached parity.

### Release recommendation

**Block release.** F-1 and F-2 are release blockers. F-3 through F-10 are each independently sufficient to fail an acceptance review of the stated requirements.

### Top ten blockers

| # | ID | Severity | Blocker |
|---|---|---|---|
| 1 | F-1 | P0 | `ReturnCaseWorkflow` is never started in production; the entire post-confirmation flow (bay, support, RMA, reminders, propagation) is dead code at runtime |
| 2 | F-2 | P0 | Order Discovery misspelling fallback reads an unfiltered, unordered `LIMIT 100` customer batch and scores it with `difflib` — capable of missing the correct order |
| 3 | F-3 | P1 | Runtime configuration and AI model/route changes never reach worker processes; workers load config once at startup and never refresh |
| 4 | F-4 | P1 | Pre-dispatch AI interception is bypassed by `structured_invocation` — the path the Order Agent and Graph Analyzer actually use |
| 5 | F-5 | P1 | Support waits/reminders are wall-clock, not business-calendar; `business_calendar_id` and `timezone` are declared and never read |
| 6 | F-6 | P1 | Bay Assignment recommends nothing on the canonical path — no warehouse, bay, return location or confidence; the activity only writes a "requested" fact |
| 7 | F-7 | P1 | RMAs are never persisted to the configured SQL return store on the canonical path |
| 8 | F-8 | P1 | Shipment/tracking management (Section 7) does not exist — no create/update API, no UI, no carrier/status/timestamp model |
| 9 | F-9 | P1 | Approvals screen is entirely absent from the frontend despite a complete backend proposal kernel |
| 10 | F-10 | P1 | Identification field catalogue is not configuration-driven; a tenth field requires Python changes in at least four places |

### Strongest areas

1. **AI privacy (Section 19)** — `redact_payload` recurses into dicts, lists *and* JSON-encoded strings, with bounded depth and a deliberate leaf-only masking rule that preserves schema metadata. Genuinely correct.
2. **Pricing integrity (Section 17)** — a missing price yields `UNKNOWN` with `amount_micros=None`, never zero. Explicitly designed and documented.
3. **Graph Analyzer independence (Section 12)** — zero imports from any returns business module. Verified exhaustively.
4. **Unified approval kernel (Section 21)** — one `ProposalKernel` with the full `DRAFT → VALIDATED → REVIEW_PENDING → APPROVED/REJECTED → ACTIVATED → SUPERSEDED` lifecycle, shared by graph-schema, improvement and configuration proposals.
5. **Order confirmation safety (Section 3.6)** — `CandidateSet.validate_selection` binds a confirmation to conversation, principal, tenant and graph generation with expiry; case creation is idempotent on `tenant | conversation | order | line-set`.
6. **Source-write safety** — SQL identifier allowlisting plus enforced sandbox host/user/database separation in `ai_studio.py`. No injection found.

### Weakest areas

1. **End-to-end runtime integration** — components are individually built and individually good, but the canonical chain is not connected (F-1, F-6, F-7).
2. **Worker-side runtime configuration** — an entire class of requirements (Sections 15, 16) is satisfied in the API process and not at all in workers.
3. **Configuration-driven extensibility of discovery** — the requirement most emphasised in the brief (Section 3.1) is the least met.
4. **Documentation truth** — several docstrings and the README assert behaviour the code does not implement, including on a security-boundary table.

### Severity counts

| Severity | Count |
|---|---|
| **P0 — Release blocker** | 2 |
| **P1 — Critical** | 8 |
| **P2 — Major** | 9 |
| **P3 — Minor** | 7 |
| **P4 — Cleanup** | 5 |
| **Total** | **31** |

---

# Section B — Requirement Coverage Scorecard

| Requirement / Capability | Status | Severity | Evidence | Notes |
|---|---|---|---|---|
| **§2.1–8** Conversation → discovery → confirmation | IMPLEMENTED | — | `dynamic_knowledge/api/order_agent.py`, `order_agent/graph_nodes.py` | Canonical `/api/v2/order-agent` path works end to end to confirmation |
| **§2.9** Exactly one durable case from confirmation | IMPLEMENTED | — | `graph_nodes.py:1002` `confirm_order`, `integration/case_store.py:99` | Idempotent on `tenant\|conversation\|order\|line-set` |
| **§2.10** Captured facts preserved, not re-asked | PARTIAL | P2 | `return_case_activities.py:141` `latest_case_facts` | Fact projection exists; no re-ask suppression proven at runtime — `NOT PROVABLE` |
| **§2.11** Missing return details gathered | NOT PROVABLE | — | — | Depends on `ReturnCaseWorkflow`, which never starts (F-1) |
| **§2.12–14** Bay concurrent, advisory, non-blocking | BROKEN | P1 | `return_case_activities.py:114`, `return_case_workflow.py:415-442` | Non-blocking is correct; nothing is ever recommended (F-6) |
| **§2.15–17** Support conversation, durable business-time waits | PARTIAL | P1 | `return_case_workflow.py:484-517` | Durable timers correct; business calendar not implemented (F-5); unreachable anyway (F-1) |
| **§2.18** Support creates one or more RMAs via UI | PARTIAL | P1 | `api/return_support.py:317`, `SupportConsolePage.tsx:379` | UI + API exist; signal targets a workflow that was never started (F-1) |
| **§2.19** RMA persisted to configured SQL return store | MISSING | P1 | `return_case_activities.py:196-228` writes Mongo only | F-7 |
| **§2.20–21** Tracking/shipment create-update UI + store | MISSING | P1 | no endpoint, no screen | F-8 |
| **§2.22** RMA/tracking/labels visible in associate conversation | BROKEN | P1 | `return_case_activities.py:234` `append_case_fact` | Mechanism is correct and never executes (F-1) |
| **§2.23** Fulfilment reads shipment through the graph | NOT PROVABLE | P2 | `sync_service.py:416` `node_shipment` | Graph node exists; no fulfilment read path proven |
| **§2.24–26** Outcomes, proposals, approvals, activation | PARTIAL | P1 | `platform/governance/kernel.py`, `proposals.py` | Backend complete; no Approvals UI (F-9) |
| **§2.27** Config change without API/worker restart | PARTIAL | P1 | `runtime_activation.py`, `main.py:1031` | API yes, workers no (F-3) |
| **§2.28** Resume a case days later with real state | PARTIAL | P2 | `coordinator.py:392,445` thread resume; `continue_as_new` | Discovery resume implemented; case resume unreachable (F-1) |
| **§3.1** Identification fields fully configuration-driven | MISSING | P1 | `order_agent/contracts.py:36-65`, `search_strategy.py:151-263` | F-10 |
| **§3.1** Tenth field without source change | MISSING | P1 | same | F-10 |
| **§3.2** Human-like, non-scripted search | PARTIAL | P2 | `search_strategy.py:151` `build_progressive_plans` | Plans are model-driven per turn but each field's plan is hardcoded |
| **§3.3** Capture facts immediately with provenance | IMPLEMENTED | — | `append_case_fact(agent_id, channel, acquisition_method, source_path)` | Provenance fields are complete |
| **§3.4** Partial/misspelled input not promoted to fact | IMPLEMENTED | — | `graph_nodes.py:787-795` score 0.6, `matches:["customer_name_fuzzy"]` | Fuzzy matches are marked, not confirmed |
| **§3.5** Complete-dataset search, no bounded fuzzy scan | **BROKEN** | **P0** | `search_strategy.py:95,466-484`; `graph_nodes.py:752` | **F-2** |
| **§3.6** Explicit confirmation, idempotent, concurrency-safe | IMPLEMENTED | — | `contracts.py:99`, `graph_nodes.py:1050` | Strong implementation |
| **§4** Durable case, append-only facts, provenance | IMPLEMENTED | — | `api/cases.py`, `operations/repository.py` | Optimistic concurrency via `expected_version` |
| **§4** Concurrent writers, CAS | IMPLEMENTED | — | `conversation_repository.py:81`, `update_return_record(expected_version=)` | |
| **§5** Case → N return records → N items | IMPLEMENTED | — | `api/cases.py:44-61` | Nested, cannot cross-contaminate |
| **§6** Support Console capabilities | PARTIAL | P2 | `SupportConsolePage.tsx` | Queue, conversation, RMA form present; no bay panel (F-6), no correction flow |
| **§6** RMA → SQL adapter → transaction → persistence | MISSING | P1 | F-7 | |
| **§7** Shipment/tracking management | MISSING | P1 | F-8 | |
| **§8** Bay recommends warehouse + bay + return location | MISSING | P1 | `return_case_workflow.py:154-159` `BayResultNotice` | No return location, no confidence (F-6) |
| **§8** Bay failure never blocks | IMPLEMENTED | — | `return_case_workflow.py:422-442` | Correct by construction |
| **§9** Durable waits/reminders | IMPLEMENTED | — | `return_case_workflow.py:484-517`, `continue_as_new` | Durability is genuinely correct |
| **§9** Business calendar / timezone honoured | **MISSING** | P1 | `return_case_workflow.py:128-129,492` | **F-5** — declared, never read |
| **§10** Sources read-only; platform stores writable | IMPLEMENTED | — | writes target `dbo.return_*`, `platform.*`, `integration.*` only | README contradicts this (D-2) |
| **§10** No arbitrary query/mutation on connectors | IMPLEMENTED | — | `source_connectors/protocols.py` | Contract is scan/point-lookup only |
| **§11** Full / incremental / on-demand sync, watermark | IMPLEMENTED | — | `sync_service.py:78-183,281,548-556` | Run history, counts, skipped sources all surfaced |
| **§11** Sync failure visible in UI | IMPLEMENTED | — | `SyncControlPage.tsx`, `api/graph_sync.py` | |
| **§12** Analyzer independent of returns modules | IMPLEMENTED | — | zero `operations/workflows/dynamic_knowledge/api/agents` imports | Verified exhaustively |
| **§12** Scope enforced programmatically | IMPLEMENTED | — | `application/source_inspection.py:150` `self._scope.sample_limit` | |
| **§12** Samples masked before model/trace | IMPLEMENTED | — | `application/sample_masking.py:107` wraps `profile` | Masking sits in front of the port |
| **§13** Lifecycle draft→…→superseded | IMPLEMENTED | — | `platform/governance/proposal.py:64-71` | Full lifecycle present |
| **§13** Change classification additive/compatible/destructive | MISSING | P2 | only `ChangeKind` ADDED/REMOVED/CHANGED | F-12 |
| **§13** Generation build → swap → drain → retire | PARTIAL | P2 | `dynamic_knowledge/graph/generation.py` exists; `sync_service.py:73,422,642,649` pins `legacy-live` | F-11 |
| **§14** env → vault → DB → packaged YAML precedence | IMPLEMENTED | — | `runtime_activation.py:169-190`, `secrets/runtime.py` | Packaged YAML never rewritten; infra changes fail closed |
| **§15** Hot config in API | IMPLEMENTED | — | `runtime_activation.py:73`, `main.py:1031` | Poll-guarded, atomic assignment boundary |
| **§15** Hot config in workers | **MISSING** | P1 | `scripts/run_order_discovery_worker.py:27,59,84` | **F-3** |
| **§15** Existing case pinned to starting release | IMPLEMENTED | — | `coordinator.py:344` `GenerationBinding.STRICT_PINNING`, `configuration_release_id` on workflow input | |
| **§16** Runtime provider/model/route change | PARTIAL | P1 | `runtime_activation.py:199-219` `replace_routes` | API only; workers stale (F-3) |
| **§17** Single AI gateway, no provider bypass | IMPLEMENTED | — | no provider SDK imports outside `ai/providers/`; all dispatch via `AIRoutePool` | Verified by exhaustive grep |
| **§17** Full request telemetry | IMPLEMENTED | — | `ai/gateway/telemetry.py`, `_attempt_metric` | provider/model/tokens/latency/cost/fallback/correlation all recorded |
| **§17** Missing pricing → null, never zero | IMPLEMENTED | — | `ai/pricing.py:117-120` | |
| **§18** Pre-dispatch interception exists | PARTIAL | P1 | `service.py:449`, `api/ai_gateway.py:329` | Real, with audit — but only on one path (F-4) |
| **§18** All invocation styles intercepted | **MISSING** | P1 | `interceptMode` appears in exactly one dispatch path | **F-4** |
| **§18** Replay / compare alternate model | IMPLEMENTED | — | `ai/providers/replay.py`, `replay_store.py`, `AiControlCenterPage.tsx:309` | |
| **§19** Recursive PII redaction incl. serialized JSON | IMPLEMENTED | — | `ai/gateway/redaction.py:55-97` | Best-in-repo implementation |
| **§19** No credential in agent/prompt/trace/browser | IMPLEMENTED | — | Vault resolution in adapters; `is_sensitive_key` allowlist | |
| **§20** Blocking vs best-effort enforced at runtime | PARTIAL | P2 | `descriptor.py:36` declared; nothing reads it | F-14 — behaviour is hardcoded per workflow instead |
| **§21** Improvement proposals under permitted-key policy | IMPLEMENTED | — | `platform/governance/key_policy.py` | Forbidden targets enumerated |
| **§21** One approval kernel | IMPLEMENTED | — | `platform/governance/kernel.py`, `ProposalType` | |
| **§22** Ten required screens | PARTIAL | P1 | `frontend/src/domains/registry.ts` | 7 domains + landing; Approvals missing (F-9), Data Sources nested (F-13) |
| **§22** Per-screen contextual sidebar | PARTIAL | P2 | `registry.ts` — 5 of 7 domains have `sections: []` | F-16 |
| **§23** UI correctness | IMPLEMENTED | — | 168/168 Vitest pass, `tsc -b` clean | No runtime UI bugs found statically |
| **§24** Workflow determinism | IMPLEMENTED | — | `workflow.now()`, `workflow.wait_condition`, ids passed in from workflow | No wall-clock or random UUID inside workflow code |
| **§24** Idempotency across writes | IMPLEMENTED | — | numbered reminders, derived `fact_id`, derived work-item id, `expected_version` | Strong throughout |
| **§25** Per-case observability feeding Case Operations UI | PARTIAL | P2 | `api/canonical_returns.py:267` timeline; `ReturnsOperationsPage.tsx` | Timeline exists for sessions; case-level agent decision log not surfaced |
| **§26** Tenant isolation / IDOR | IMPLEMENTED | — | `api/cases.py:89-96` `_belongs_to`, `conversation_repository.py:55-60` | tenant **and** principal, not either |
| **§26** SQL/Cypher injection | IMPLEMENTED | — | `ai_studio.py:1161-1163` `_SAFE_IDENTIFIER`; parameterized Cypher | No injection found |
| **§27** Performance at production scale | PARTIAL | P1 | F-2, plus per-signal query fan-out | See Section P |
| **§28** Idempotent, resumable bootstrap | IMPLEMENTED | — | `bootstrap/system_store.py`, `apply_neo4j_migrations.py:103` drift check | Fails closed on index drift |
| **§28** Workers verify real registration | IMPLEMENTED | — | `run_order_discovery_worker.py:47-50,92-99` ping + verify + heartbeat | |
| **§29** Adversarial test coverage | PARTIAL | P2 | 2,793 tests; several mandatory scenarios absent | See Section R |
| **§30** Documentation correctness | PARTIAL | P2 | README, `confirm_order` docstring, `ReturnCaseTimingConfiguration` docstring | See Sections S/T |
| **§31** Exactly one implementation per concern | PARTIAL | P2 | `test_frozen_modules_gain_no_new_callers.py` | Two Order Discovery, two AI packages — governed but present |

---

# Section C — Critical Findings (P0 / P1)

## F-1 — `ReturnCaseWorkflow` is never started in production

| Field | Value |
|---|---|
| **Requirement** | §2.12–22 — bay assignment, support conversation, durable waits, RMA creation, propagation to the associate |
| **Status** | BROKEN |
| **Severity** | **P0 — Release Blocker** |

**Code evidence**

- `backend/src/return_platform/workflows/return_case_workflow.py:314` — `class ReturnCaseWorkflow` is fully implemented (bay request + wait, support draft, work item, reminder cadence, outcome recording, graph sync, `continue_as_new`).
- `backend/src/return_platform/workflows/worker.py:48` — the workflow is *registered* on the return-workflow worker when case activities are supplied.
- `backend/src/return_platform/api/return_support.py:371` — Support's RMA submission does `resources.temporal.get_workflow_handle(return_case_workflow_id(item.caseId))` then `.signal("support_response", …)`.
- **`ReturnCaseWorkflow.run` is passed to `start_workflow` in exactly one file in the repository: `backend/tests/test_return_case_workflow_real_infra.py:172`.**
- `backend/src/return_platform/dynamic_knowledge/api/order_agent.py:223-228` — the only workflow the canonical discovery API starts is `OrderDiscoveryWorkflow`.
- `backend/src/return_platform/dynamic_knowledge/order_agent/graph_nodes.py:1023-1026` — the confirmation node's own docstring states: *"Starting the case's durable workflow is deliberately not here: `ReturnCaseWorkflow` does not exist yet."* That statement is now false; the workflow exists, and nothing was added to start it.

**Runtime path**

`POST /api/v2/order-agent/conversations/{id}/turns` → `OrderDiscoveryWorkflow.submit_turn` → `confirm_order` node → `RepositoryCaseStore.create_case` (MongoDB) → **stop**. No Temporal case workflow is started.

**Current behaviour**

A confirmed order produces a durable case document and nothing else. No bay is requested, no support work item opens, no reminder timer is armed, no RMA can be recorded.

**Required behaviour**

Confirmation must start exactly one `ReturnCaseWorkflow` per case (idempotently, keyed by `return_case_workflow_id(case_id)`), which then drives bay assignment concurrently, opens the support channel, and records Support's RMA outcome back onto the case.

**Failure scenario**

1. Associate confirms an order. A case is created.
2. Support opens the console, sees the work queue — which is populated only by workflow-created work items, so the case never appears.
3. If a work item is reached by any other route and Support submits an RMA, `get_workflow_handle(...).signal(...)` is issued against a workflow ID that was never started. Temporal raises `NOT_FOUND`; the endpoint returns 500. The RMA is lost.
4. Steps 12–22 of the required business flow never occur.

**Test coverage**

`backend/tests/test_return_case_workflow_real_infra.py` exercises the workflow thoroughly — including `test_a_bay_result_arriving_before_the_wait_is_kept` (line 304) — by **starting the workflow itself**. No test asserts that the *application* starts it. This is precisely the false-confidence class the brief describes: the workflow is proven correct in isolation and never proven connected.

**Documentation status**

`graph_nodes.py:1023-1026` actively asserts the opposite of current reality and is the likely reason the gap survived review.

**Why the current implementation is insufficient**

Every downstream capability is built, tested and unreachable. This is not a partial implementation — it is a disconnected one. No amount of correctness in `ReturnCaseWorkflow` matters while no caller starts it.

---

## F-2 — Bounded, unordered `difflib` customer probe can miss the correct order

| Field | Value |
|---|---|
| **Requirement** | §3.5 — complete-dataset search; "any bounded fuzzy strategy capable of missing the correct order is P0" |
| **Status** | BROKEN |
| **Severity** | **P0 — Release Blocker** |

**Code evidence**

- `backend/src/return_platform/dynamic_knowledge/order_agent/search_strategy.py:95` — `FUZZY_CUSTOMER_PROBE_LIMIT = 100`
- `search_strategy.py:466-484` — `build_customer_fuzzy_probe_plan()` returns `LogicalQueryPlan(operation=SEARCH, start_entity_id="customer", fields=(...), limit=100)` with **no `filters` and no `sort`**.
- `backend/src/return_platform/dynamic_knowledge/knowledge/cypher_compiler.py:154` — `ORDER BY` is emitted only when sort parts exist. This plan supplies none, so the compiled Cypher is an unordered `MATCH (c:Customer) … LIMIT 100`.
- `search_strategy.py:506-513` — `_similarity` uses `difflib.SequenceMatcher` client-side.
- `backend/src/return_platform/dynamic_knowledge/order_agent/graph_nodes.py:739-801` — `_fuzzy_customer_fallback` is invoked from the live `order_search` node at line 678 whenever `total_found == 0` and customer names are present.

**Runtime path**

`/api/v2/order-agent/.../turns` → `order_search` node → progressive plans return zero rows → `_fuzzy_customer_fallback` → unfiltered 100-row read → `fuzzy_match_customers` → candidates scored 0.6.

**Current behaviour**

Neo4j returns an arbitrary, storage-order-dependent 100 customers. The misspelled name is compared only against those 100.

**Required behaviour**

Indexed approximate search across the complete customer set. The correct mechanism **already exists and is already migrated**: `backend/src/return_platform/data_platform/graph/migrations/0013_order_discovery_fulltext_v2.cypher` creates `customer_name_search_v2` on `Customer.customer_name`, and `apply_neo4j_migrations.py:99-103` verifies it is ONLINE at bootstrap.

**The index is used only by the frozen implementation.** `backend/src/return_platform/operations/associate_flow.py:1385-1432` calls `CALL db.index.fulltext.queryNodes($indexName, $query, {limit: $limit})` using `progressive.customer_fulltext_index`. That module is FROZEN and deprecated (`api/associate_returns.py:3`). The canonical agent never issues a full-text query — grep for `db.index.fulltext` across `backend/src` returns hits only in `associate_flow.py`.

**Failure scenario**

Customer table holds 250,000 rows. Associate types `Jhon Smi`. The exact/CONTAINS pass returns zero. The probe reads customers 1–100 in storage order. `John Smith` is row 84,000. `fuzzy_match_customers` returns `[]`. The agent reports no match; the associate is told the order cannot be found, and the return is abandoned or raised against the wrong order after further guessing.

Probability of recovery ≈ 100/N. At the seed dataset's scale this passes; at production scale it fails almost always. This is exactly the "dataset too small to expose search bugs" trap the brief names.

**Test coverage**

`backend/tests/dynamic_knowledge/test_search_strategy.py:177-215, 394` tests `fuzzy_match_customers` against **hand-supplied row lists** and asserts `build_customer_fuzzy_probe_plan()` carries `limit == 100`. It therefore asserts the defect as intended behaviour and cannot detect it. Verified passing (43 tests) during this audit.

**Documentation status**

`search_strategy.py:91-95` documents the limitation honestly and justifies it with *"Neo4j has no built-in edit-distance function (that's an APOC extension, not installed here), so a misspelled name can't be resolved with a single server-side query."* The premise is wrong: a full-text index does not need APOC, and the repository already creates one and already queries it from the other implementation.

**Why the current implementation is insufficient**

A search that can silently return "not found" for a record that exists is a correctness failure, not a performance one. The remedy requires no new infrastructure — only routing the canonical fallback through the existing `customer_name_search_v2` index.

---

## F-3 — Runtime configuration and AI model changes never reach workers

| Field | Value |
|---|---|
| **Requirement** | §15, §16, §29 adversarial #30/#31 |
| **Status** | MISSING |
| **Severity** | P1 |

**Code evidence**

- `backend/src/return_platform/configuration/runtime_activation.py:49` — `RuntimeConfigurationActivator` implements correct hot activation: 5-second poll guard, head-revision comparison, Vault re-resolution, `replace_routes` on the live `AIRoutePool`, atomic assignment boundary (lines 237-251).
- It is instantiated in **one place**: `backend/src/return_platform/main.py:569` (the FastAPI app), and refreshed from request middleware at `main.py:1031-1033`.
- `backend/scripts/run_order_discovery_worker.py:27` — `runtime = await resolve_process_configuration()` — once.
- Line 59-66 — `AIRoutePool(build_routes(settings), runtime.ai_gateway_configuration.configuration)` — built once.
- Line 84 — `schema = load_active_schema(settings.dynamic_knowledge_schema_path)` — loaded once, from a packaged YAML path.
- Line 103 — `await worker.run()` — runs indefinitely. The only loop in the process is the heartbeat (lines 92-99).
- `backend/scripts/run_return_workflow_worker.py:32` — same single `resolve_process_configuration()` call; the `while True` at line 101 is also the heartbeat.
- No worker constructs `RuntimeConfigurationActivator` (verified by repository-wide grep).

**Runtime path**

Admin activates a release → `release_promotion.py:182` `activator.refresh(force=True)` → API process adopts → **workers continue on their startup snapshot indefinitely**.

**Failure scenario**

The Order Agent's reasoning turns execute in the worker (`OrderDiscoveryActivities.run_order_discovery_turn`). An administrator switches the discovery model from Gemini to Anthropic. The AI Control Centre confirms the new route is active. Every subsequent agent turn still calls Gemini, because the worker's `AIRoutePool` was built at startup. The same applies to agent settings, clarification policy, prompt versions and the active graph schema.

Worse, API and workers now disagree about configuration, so behaviour depends on which process serves a given step.

**Required behaviour**

Workers must run the same activator (or an equivalent watch) and rebuild their route pool, schema and configuration snapshot without restart.

**Test coverage** — none. Adversarial scenarios #30 and #31 are unimplemented.

---

## F-4 — Pre-dispatch interception is bypassed by the canonical invocation path

| Field | Value |
|---|---|
| **Requirement** | §18 — "If one invocation style bypasses interception, requirement is incomplete" |
| **Status** | PARTIAL |
| **Severity** | P1 |

**Code evidence**

- `backend/src/return_platform/ai/gateway/service.py:449-455` — the *only* interception gate:
  ```python
  if gateway_settings.interceptMode and force_provider is None:
      trace = await self._repository.update_ai_trace(..., INTERCEPTION_PENDING)
      return GatewayEvaluation(trace=trace, pending_interception=True)
  ```
- Repository-wide grep for `interceptMode` yields exactly one dispatch-time reader: `service.py:449`.
- `backend/src/return_platform/ai/gateway/structured_invocation.py` contains **no** occurrence of `interceptMode`, `INTERCEPTION_PENDING` or `intercept` (verified). It dispatches at line 433 via `route.provider.generate(...)`.
- `backend/src/return_platform/dynamic_knowledge/integration/model_gateway.py:17-18` — the Order Agent's gateway imports `structured_invocation`.
- `backend/src/return_platform/bootstrap/adapters/analyzer_ai_adapter.py:9,39` — the Graph Analyzer likewise, and its own comment says *"the same path the Order Agent uses"*.
- `backend/src/return_platform/ai/gateway/redaction.py:104-105` confirms: *"structured_invocation, which is the path the Order Agent actually uses."*

**What does work**

`api/ai_gateway.py:329-424` is a genuinely good interception API: `EDIT_AND_DISPATCH` (dev/test only), `CANCEL`, and manual answer with `MANUAL_OVERRIDE`, all with `interceptedBy`, `interceptionReason`, optimistic `expectedVersion`, and `append_audit`. `AiControlCenterPage.tsx` surfaces an Interceptions section.

**The gap**

That machinery guards `AIGatewayService.evaluate`, whose production callers are `operations/orchestrator.py` and the frozen `operations/associate_flow.py`. The two paths the brief names explicitly — Order Discovery and Graph Analyzer — dispatch to providers with no interception check at all.

`DurableInterceptionProvider` (`ai/providers/durable_interception.py`) is *not* a substitute: it is a MANUAL **provider** that replaces the model rather than gating dispatch to one, and it is hard-gated to `settings.environment in {"development","test"}` (line 68).

**Failure scenario**

An operator enables intercept mode to review what the platform sends about customers before it reaches a third-party provider. Every eligibility call is held. Every Order Discovery turn — the path that actually carries the transcript and retrieved graph rows — dispatches straight through, unheld and unreviewed.

---

## F-5 — Support waits are wall-clock; business calendar and timezone are declared and never read

| Field | Value |
|---|---|
| **Requirement** | §9 — "Verify timer calculation actually uses configured business days/hours/calendar/timezone" |
| **Status** | MISSING |
| **Severity** | P1 |

**Code evidence**

- `backend/src/return_platform/configuration/return_configuration.py:296-298` documents: *"Support durations are business-calendar durations. Eight hours means eight working hours, which over a weekend is a different wall-clock instant entirely; `business_calendar_id` names the calendar that decides."*
- Lines 311-312 define `business_calendar_id: NonBlank = "default"` and `timezone: NonBlank = "UTC"`.
- `backend/src/return_platform/workflows/return_case_workflow.py:128-129` carries both into `ReturnCaseTimings`.
- **Repository-wide grep for `business_calendar_id` and `timezone` under `backend/src/return_platform/workflows/` returns only those two declaration lines.** Neither is read anywhere.
- `return_case_workflow.py:492` — `deadline = workflow.now() + timedelta(seconds=timings.support_response_wait_seconds)` — plain wall-clock.
- Line 497 — `interval = timedelta(seconds=timings.reminder_interval_seconds)` — plain wall-clock.
- No `BusinessCalendar` class, holiday table or working-hours function exists anywhere in the repository.

**Failure scenario**

Config: 8-hour support wait, 2-hour reminders, max 3, `PARK_FOR_OPERATIONS` on exhaustion. Associate raises a return Friday 16:30. Reminders fire 18:30, 20:30, 22:30 — into an empty Friday night queue. At 00:30 Saturday the reminders are exhausted and the case parks for operations. No human sees it until Monday, and the case is now in a terminal parked state rather than awaiting support.

**What is correct**

Durability is properly done: `workflow.wait_condition` produces durable Temporal timers, `continue_as_new` handles multi-day waits (line 513-517), and reminders are numbered so a replay re-sends the same one (line 534-536). Only the calendar arithmetic is missing.

---

## F-6 — Bay Assignment recommends nothing on the canonical path

| Field | Value |
|---|---|
| **Requirement** | §8 — warehouse + bay + return location as one coherent result, with computed confidence |
| **Status** | BROKEN |
| **Severity** | P1 |

**Code evidence**

- `backend/src/return_platform/workflows/return_case_activities.py:114-131` — `request_bay_assignment` executes exactly one statement: `append_case_fact(fact_name="bay_assignment_requested", value=True)`. It queries no graph, consults no bay availability, resolves no location and returns nothing.
- `return_case_workflow.py:433-442` — the workflow then waits `bay_wait_seconds` for a `bay_result` signal.
- `return_case_workflow.py:328-329` declares `@workflow.signal(name="bay_result")`. The only sender in the repository is `backend/tests/test_return_case_workflow_real_infra.py:314`.
- `return_case_workflow.py:154-159` — `BayResultNotice` carries `warehouse_reference`, `bay_reference`, `reason`. **No `return_location`. No `confidence`.**

**Real logic exists, unconnected.** `backend/src/return_platform/operations/warehouse/service.py` (`WarehousePlacementService`) and `dynamic_knowledge/integration/bay_observations.py` (`GraphWarehouseBayObservations`) implement graph-backed bay candidate selection, exposed at `POST /api/v1/warehouse/returns/{session_id}/bay-recommendation`. It is keyed by **session**, not by **case**, and the case workflow never calls it.

**Failure scenario**

Every case waits the full 120 seconds, times out, logs `"bay wait elapsed; proceeding without a bay"`, and proceeds with no placement. Because F-1 means the workflow never runs at all, in practice not even the fact is written.

**Note on grading** — the best-effort/non-blocking requirement (§2.14, §8 final bullet) *is* correctly implemented: `_BEST_EFFORT_RETRY`, `ActivityError` caught and recorded rather than raised, timeout treated as non-error. The defect is that there is nothing to be best-effort about.

---

## F-7 — RMAs are never persisted to the configured SQL return store

| Field | Value |
|---|---|
| **Requirement** | §2.19, §6 — "RMA must persist into the configured SQL return store" |
| **Status** | MISSING |
| **Severity** | P1 |

**Code evidence**

- `backend/src/return_platform/workflows/return_case_activities.py:196-243` — `record_support_outcome` calls `self._repository.create_return_record(...)`, `update_return_record(...)` and `append_case_fact(...)`. `self._repository` is `OperationalRepository`, constructed over `AsyncMongoClient` (`run_order_discovery_worker.py:89`). All writes are MongoDB.
- `return_case_activities.py:274-324` — `synchronize_return_records` projects records into **Neo4j**.
- The SQL return store writer is `backend/src/return_platform/operations/sql_business_state.py:325` `persist_support_result`, which inserts into `dbo.return_requests`, `dbo.return_items`, `dbo.return_fulfillment`, `dbo.return_tracking` and `integration.return_support_ticket`.
- Its only callers are `operations/return_support/providers/sandbox.py:43` and `providers/external.py:133` — the **legacy session-based** support providers, not the case workflow.

**Current behaviour** — canonical RMAs live in MongoDB and Neo4j. The SQL tables that the schema migrations create specifically for returns (`001_return_business_state.sql`, `002_domain_models.sql`, `003_production_return_platform.sql`) are populated only by the legacy path and by seeding.

**Failure scenario** — any downstream consumer, report or integration reading `dbo.return_requests` as the system of record sees no RMAs from the current product.

---

## F-8 — Shipment / tracking management does not exist

| Field | Value |
|---|---|
| **Requirement** | §7 — create/update shipment & tracking via simple UI, persisted to the ShipmentInfo/shipment store |
| **Status** | MISSING |
| **Severity** | P1 |

**Code evidence**

- No create or update endpoint for shipments exists. Grep for `shipment` across `backend/src/return_platform/api/` yields only reads: `return_artifacts.py:82` and `canonical_returns.py:348`, both `list_shipment_events(session_id)`.
- No tracking/shipment screen exists. Grep across `frontend/src/**/*.tsx` finds a single `trackingReference` text input inside the Support Console's RMA form (`SupportConsolePage.tsx:379,426,440`) and read-only display in `ReturnCopilotPage.tsx:891`.
- The configuration-driven field set required by §7 — carrier, shipment status, status timestamp, shipment details — has no model. `ReturnOutcomeRecord` (`api/return_support.py:289-299`) carries only `trackingReference`, `labelReference`, `returnLocation`, `shippingInstructionReference`.
- `dbo.return_tracking` exists in `002_domain_models.sql:42` and is written only by the legacy `persist_support_result`.
- `config/returns/production.yaml` declares `shipment_collection: shipmentInfo` and `tracking_field: shipmentInfoEventData.trkNum`, i.e. shipment is treated as an externally-owned read-only source — consistent with §10 but not with §7's requirement for operator-created/updated records.

**Consequence** — §7's entire trace (`Tracking UI → API → shipment service → adapter → shipment store → graph sync → fulfilment agent → UI`) is absent. Out-of-order status updates, duplicate updates and RMA association cannot be audited because no such write path exists.

---

## F-9 — Approvals screen is absent

| Field | Value |
|---|---|
| **Requirement** | §22.10 — unified approvals inbox |
| **Status** | MISSING |
| **Severity** | P1 |

**Code evidence**

- `frontend/src/domains/registry.ts:97-195` defines seven domains: `/returns`, `/support`, `/config`, `/graph-schema`, `/ai`, `/sync`, `/operations`. There is no `/approvals`.
- `frontend/src/domains/` contains no approvals directory (full listing verified).
- Grep for `approval|proposal` across `frontend/src` matches only generated types, MSW mocks, and incidental strings in `graphSchema.ts` / `GraphSchemaPage.tsx`.
- The backend is complete: `main.py:1221` mounts `governance_proposals_router`; `api/proposals.py` (10,998 bytes) exposes the surface; `platform/governance/kernel.py` implements `ProposalKernel`; `proposal.py:64-71` carries the full lifecycle; `key_policy.py` enforces forbidden targets.

**Consequence** — §2.26 ("authorized reviewers approve/reject proposals") has no user interface. Improvement proposals can be generated and can never be reviewed. The requirement for before/after/diff, evidence, risk, validation receipt, affected keys and decision history is unmet at the UI layer.

---

## F-10 — Identification field catalogue is not configuration-driven

| Field | Value |
|---|---|
| **Requirement** | §3.1 — "The system must allow a tenth identification field without changing Python or TypeScript source" |
| **Status** | MISSING |
| **Severity** | P1 |

**Code evidence**

Adding one identification field to the canonical path requires editing at minimum:

1. `dynamic_knowledge/order_agent/contracts.py:36-65` — `OrderSearchIntent` is a frozen Pydantic model with `extra="forbid"` and 17 hardcoded field names.
2. `dynamic_knowledge/order_agent/search_strategy.py:102-120` — `_SIGNATURE_FIELDS`, a hardcoded tuple used for pagination identity.
3. `search_strategy.py:151-263` — `build_progressive_plans`, a numbered sequence of per-field branches with hardcoded `start_entity_id`, `field_id`, `operator` and `limit` (`sales_order/sales_order_number/EXACT/1`, `customer/customer_name/CONTAINS/5`, `product/sku/EXACT/5`, …).
4. `search_strategy.py:443-463` — `_location_signals`, hardcoded field/operator pairs.
5. `search_strategy.py:50` — `_UNSUPPORTED_INTENT_FIELDS`.
6. `search_strategy.py:77-78` — `_DATE_FIELD_ENTITY` / `_DATE_FIELD_ID`.
7. `rank_search_results` — per-field scoring.

**The configuration exists but is bound to the frozen implementation.** `config/returns/production.yaml` defines a rich `discovery:` block (`anchor_weights`, `strong_anchors`, `anchor_extractors`, `free_text_fallback_anchor`, `progressive.*`) and `clarification_policy` with `field_selection_owner: LLM`. Grep shows these keys are consumed by `operations/associate_flow.py`, `agents/order_discovery.py` and `agents/order_analysis.py` — **none of which is the canonical `dynamic_knowledge/order_agent/` path.** The canonical `coordinator.py` reads only `schema.agent_policies`, `configuration_release_id` and `policy_version`.

**Consequence** — the same inversion as F-2 and F-4: the deprecated implementation is the configuration-driven one, and the shipped one is hardcoded. Adversarial scenario #37 ("tenth discovery field added without source change") cannot pass.

---

# Section D — Dynamic Order Discovery Audit

| Aspect | Finding |
|---|---|
| **Configured vs hardcoded fields** | Hardcoded on the canonical path (F-10). The configuration-driven catalogue exists in `production.yaml` and drives only the frozen `associate_flow`. |
| **Tenth-field extensibility** | Not achievable without Python edits in ≥4 locations (F-10). |
| **Initial fact capture** | Implemented. `append_case_fact` records `agent_id`, `channel` (`CHANNEL_A`/`CHANNEL_B`/`SYSTEM`), `acquisition_method` (`OBSERVED`/`DERIVED`), `source_path`/`source_system` and a derived `fact_id`. Provenance is complete and superseding is append-only. |
| **Return reason not re-asked** | Fact projection exists (`latest_case_facts`); suppression logic not proven at runtime. `NOT PROVABLE` (case workflow never runs — F-1). |
| **Partial-name confirmation safety** | Correct. Fuzzy hits are emitted with `score: 0.6` and `matches: ["customer_name_fuzzy"]` (`graph_nodes.py:787-795`) rather than promoted to confirmed facts. Adversarial #35 is structurally prevented. |
| **Complete-dataset search** | **BROKEN (F-2).** Unfiltered, unordered `LIMIT 100` + client-side `difflib`. The `customer_name_search_v2` full-text index exists, is bootstrap-verified, and is queried only by the frozen module. |
| **Next-action selection** | Model-driven per turn (`AgentAction`, `max_graph_queries_per_turn` budget at `graph_nodes.py:624`), with replanning on zero results. Not a fixed questionnaire — §3.2 substantially met at the *sequencing* level even though each field's plan shape is hardcoded. |
| **Dynamic aggregation / ranking** | `cypher_compiler.py:206,224` supports COUNT/DISTINCT with `ORDER BY count DESC`. `rank_search_results` merges independent per-signal passes. |
| **Pagination** | Sound: `MAX_CACHED_CANDIDATES = 25`, `RESULT_PAGE_SIZE = 5`, cached candidate set with `search_intent_signature` so "show more" pages a cached set rather than re-querying. |
| **Explicit confirmation** | Correct and strong. `auto_confirmation_allowed: false`; `CandidateSet.validate_selection` binds candidate → conversation, principal, tenant, graph generation, with 30-minute expiry (`graph_nodes.py:706-721, 1050-1057`). |
| **Idempotency / concurrency** | Correct. `OrderConfirmation.idempotency_key(tenant_id, conversation_id)` includes the line set; case creation is idempotent on `tenant\|conversation\|order\|line-set`; turn-level idempotency with `IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_MESSAGE` guard (`conversation_repository.py:103-106,177-182`). Adversarial #1 and #2 are addressed. |
| **Unsupported signals surfaced** | Good practice: `unsupported_signals()` names signals that produced no plan rather than dropping them silently. |

---

# Section E — Case / Workflow / RMA Audit

| Aspect | Finding |
|---|---|
| **Case durability** | Implemented. Case documents in platform MongoDB with `tenantId`/`principalId`, append-only `case_facts`, `returnRecordCount`. |
| **Provenance** | Implemented and complete (agent, channel, acquisition method, source system, timestamp, derived id). |
| **Optimistic concurrency** | Implemented. `update_return_record(..., expected_version=0)`, `update_ai_trace(..., expected_version=)`, conversation-level version guard. |
| **Resume** | Discovery resume is real: `coordinator.py:392,445` re-registers the Temporal thread idempotently; `CheckpointRetentionPolicy` retains agent state — not transcript replay. Case-level resume is unreachable (F-1). |
| **Multi return record / multi RMA** | **Structurally correct.** `api/cases.py:44-61` nests `items` inside each `CaseReturnRecord`, with `unassignedItems` kept separate rather than folded into the first record. Label/tracking/location live on the record, so RMA-A's label cannot attach to RMA-B. `SupportReturnRecord` is a tuple, explicitly to allow several RMAs per reply. §5 is met at the model level. |
| **Support RMA creation** | UI and API exist (`SupportConsolePage.tsx`, `api/return_support.py:317`). Signal-not-write is the right design. Broken by F-1; not persisted to SQL (F-7). |
| **SQL persistence** | MISSING on the canonical path (F-7). |
| **ShipmentInfo updates** | MISSING entirely (F-8). |
| **Propagation to associate conversation** | Mechanism is correct — `append_case_fact(fact_name="return_reference", channel=CHANNEL_B)` feeds the agent's turn context, so the RMA surfaces in the original conversation with no client-side join. Unreachable (F-1). |
| **Duplicate support response** | Handled in the workflow rather than the API (documented at `return_support.py:336-339`). Adversarial #5 addressed in design; unverifiable at runtime. |

---

# Section F — Bay / Fulfilment Audit

| Aspect | Finding |
|---|---|
| **Warehouse recommendation** | MISSING on canonical path (F-6). `WarehousePlacementService` exists but is session-keyed and uncalled by the case flow. |
| **Bay recommendation** | MISSING on canonical path (F-6). |
| **Return location** | MISSING from the bay result model entirely — `BayResultNotice` has no such field. Return location is only ever set manually by Support on `SupportReturnRecord`. |
| **Confidence** | MISSING — no confidence field, constant or computed, on the bay result. |
| **Reason category / omission reason** | PARTIAL — `BayResultNotice.reason` exists and is populated with `"REQUEST_FAILED"` on activity error only. |
| **Best-effort, never blocks** | IMPLEMENTED and correct (`_BEST_EFFORT_RETRY`, caught `ActivityError`, non-error timeout). |
| **Warehouse refreshed by on-demand sync** | PARTIAL — `MongoTargetedSyncRunLedger` and targeted sync exist and are wired into the order-agent runtime; no warehouse-specific refresh on the bay path. |
| **Graph queried for bay data** | Implemented in `GraphWarehouseBayObservations`, unreachable from the case flow. |
| **Fulfilment reads shipment through graph** | `NOT PROVABLE`. `sync_service.py:416` maintains a `node_shipment` graph node; no fulfilment agent read path was located. |

---

# Section G — Runtime Configuration Matrix

| Configuration | Runtime Editable | Restart Required | API Adoption | Worker Adoption | Existing Case | New Case | Correct |
|---|---:|---:|---|---|---|---|---|
| Conversation tone / prompts | Yes | No | ✅ `runtime_activation` | ❌ startup-only | Pinned | New release | ❌ (F-3) |
| Support wait / reminder cadence / max | Yes | No | ✅ | ❌ | Pinned (correct) | New release | ❌ (F-3) |
| Business calendar / timezone | Yes | No | ✅ stored | ❌ | — | — | ❌ **never read at all** (F-5) |
| Discovery field catalogue | Config exists | — | ✅ frozen path only | ❌ | — | — | ❌ (F-10) |
| Aliases / anchor extractors | Yes | No | ✅ frozen path only | ❌ | — | — | ❌ (F-10) |
| Ranking / selectivity / scoring | Yes | No | ✅ frozen path only | ❌ | — | — | ❌ (F-10) |
| Clarification policy | Yes | No | ✅ frozen path only | ❌ | — | — | ❌ (F-10) |
| Agent settings (timeouts, retries, concurrency) | Yes | No | ✅ | ❌ | Pinned | New release | ❌ (F-3) |
| Agent `failure_policy` | Yes | — | ⚠️ stored, never read | ❌ | — | — | ❌ (F-14) |
| Source bindings | Yes | No | ✅ | ❌ | — | — | ❌ (F-3) |
| Connectors | Yes | No | ✅ | ❌ | — | — | ❌ (F-3) |
| Permissions / capabilities | Yes | No | ✅ | ❌ | — | — | ❌ (F-3) |
| AI routing / provider / model | Yes | No | ✅ `replace_routes` | ❌ **stale pool** | Pinned | New release | ❌ (F-3) |
| AI model parameters (permitted) | Yes | No | ✅ | ❌ | — | — | ❌ (F-3) |
| AI pricing version | Yes | No | ✅ | ❌ | — | — | ❌ (F-3) |
| Sync settings | Yes | No | ✅ | ❌ | — | — | ❌ (F-3) |
| Active graph schema | Yes | No | ✅ | ❌ file-loaded at startup | Pinned via `STRICT_PINNING` | New release | ❌ (F-3) |
| Mongo/Neo4j/SQL/Valkey/Temporal endpoints | No | **Yes** | fails closed | fails closed | — | — | ✅ correct (`_RESTART_REQUIRED_SETTINGS`) |
| Secrets | Vault only | No | ✅ re-resolved | ❌ | — | — | ⚠️ API only |

**Verdict:** release pinning for in-flight cases is correctly implemented; the API adopts changes properly and atomically; **no runtime configuration reaches any worker without a restart.**

---

# Section H — AI Architecture Audit

| Requirement | Status | Evidence |
|---|---|---|
| No direct provider calls from agents | ✅ IMPLEMENTED | Zero module-level imports of `openai`/`anthropic`/`google.generativeai`/`ollama`/`cohere`/`mistralai`/`litellm` anywhere in `backend/`. Provider base URLs appear only in `configuration/settings.py:183-230` (allowed-hosts list + adapter base URLs). |
| Single canonical dispatch boundary | ✅ IMPLEMENTED | Both invocation styles converge on `AIRoutePool` → `AIProvider.generate`. `service.py:96`, `structured_invocation.py:221,433`. |
| Runtime model/provider switch | ⚠️ PARTIAL | `runtime_activation.py:199-219` rebuilds routes and calls `replace_routes` in place. API only (F-3). |
| Pricing & metrics | ✅ IMPLEMENTED | `_priced_trace_fields` writes all four pricing columns from one estimate; `_attempt_metric` records provider, model, task, tier, route, attempt, status, fallback reason, latency, rate-limit wait, tokens, digests, prompt version. |
| Missing pricing → null, not zero | ✅ IMPLEMENTED | `pricing.py:117-120` `AICostEstimate` with `status=UNKNOWN, amount_micros=None, currency=None, pricing_version=None`; documented at lines 15-17, 57-58. |
| Recursive PII redaction | ✅ IMPLEMENTED | `redaction.py:55-97`. Recurses dicts, lists **and JSON-encoded strings**; `_MAX_DEPTH=12`; masks scalars under sensitive keys only, preserving schema metadata objects; `None` stays `None` so absence is not signalled as presence. |
| Interception exists | ⚠️ PARTIAL | Real and well-audited on one path (`service.py:449`, `api/ai_gateway.py:329-424`). |
| Interception covers all invocation styles | ❌ MISSING | **F-4.** Order Discovery and Graph Analyzer bypass it. |
| Human answer on model's behalf | ✅ IMPLEMENTED | `MANUAL_OVERRIDE` action with `interceptedBy`, `interceptionReason`, audit entry. |
| Allow dispatch / reject | ⚠️ PARTIAL | `EDIT_AND_DISPATCH` is dev/test-only (`api/ai_gateway.py:348-352`); `CANCEL` works everywhere. A plain "allow unchanged" action is not distinctly modelled. |
| Replay / compare alternate model | ✅ IMPLEMENTED | `ai/providers/replay.py`, `replay_store.py`, `SystemStoreReplayStore`; UI at `AiControlCenterPage.tsx:309`. |
| Fallback | ✅ IMPLEMENTED | Route pool failover with `record_failure`/`record_success`, tier escalation, `maximumAttemptsPerRoute` / `maximumTotalAttempts`, global deadline. |
| Simulation | ✅ IMPLEMENTED | `SimulatorProvider`; `dependency_simulation/` package with its own API. |
| Manual provider deployment safety | ✅ IMPLEMENTED | `durable_interception.py:68` and `manual.py` both gate on `environment in {"development","test"}`. |
| Human output not laundered as model output | ✅ IMPLEMENTED | `DurableInterceptionProvider` reports `MANUAL`/`manual-human-v1`, never the replaced provider (documented lines 14-16). |
| Custom system prompts blocked in production | ✅ IMPLEMENTED | `service.py:383-387`. |

---

# Section I — Graph Analyzer Independence

## Standalone Readiness Score: **82 / 100**

**Verification method** — every `from return_platform.*` import across all files under `backend/src/return_platform/graph_schema_analyzer/` was enumerated. Result: imports resolve to `graph_schema_analyzer.*`, `platform.*` and `security.*` **only**. There is not a single import from `operations`, `workflows`, `dynamic_knowledge`, `api`, `agents`, `conversation`, `data_platform` or `configuration`.

**Required dependency direction is respected.** `Returns Platform → Analyzer` holds; `Analyzer → Returns Platform` does not exist.

### Complete external dependency set (the extraction blockers)

| Import | Purpose | Blocker weight |
|---|---|---|
| `platform.system_store.repository.SystemStore` | persistence port | Low — already a port |
| `platform.secrets.envelope.EnvelopeEncryptor/EnvelopePayload` | sealing samples at rest | Low |
| `platform.redaction.allowlist.AllowlistRedactor` | redaction | Low |
| `platform.redaction.sample_masking.SampleMasker` | sample masking | Low |
| `platform.governance.kernel.ProposalKernel`, `NoActivatorRegistered` | approval kernel | **Medium** — shared kernel is required by §21, so extraction must either vendor it or depend on a published contract |
| `platform.governance.proposal.ProposalStatus/ProposalType` | lifecycle enums | Medium |
| `platform.governance.errors.ActivationRefused/GovernanceError` | error types | Low |
| `platform.modules.contracts`, `platform.modules.descriptor.ModuleDescriptor/ModuleKind` | module registration | Low |
| `platform.capabilities.contracts.CapabilityRegistry` | capability registry | Low |
| `platform.contracts.epoch.RuntimeEpoch` | epoch contract | Low |
| `security.authorization.require_capability` | authz decorator | **Medium** — couples the analyzer's HTTP layer to the host's auth |
| `security.capabilities`, `security.principal.Principal` | principal model | Medium |

### Capability ownership (all ✅)

Source/object/field selection, scope enforcement, metadata inspection, index & relationship listing, profiling, sampling, masking, context assembly, analyzer conversation, schema proposal, validation, comparison, index recommendations, sync-plan generation — all owned inside the package (`application/`, `domain/`, `reasoning/`, `ports/`, `persistence/`).

### Connector contract (✅)

`ports/source_port.py` exposes validate / list sources / list objects / describe / list indexes / list relationships / profile / sample. **No arbitrary query and no mutation method exists on the contract** — verified by grep for `execute`/`run_query`/`raw_query` under `source_connectors/`: no matches.

### Scope enforcement (✅)

`application/source_inspection.py:150` routes every `profile` call through `self._scope.sample_limit(source_id=..., requested=...)`. `domain/source_scope.py` holds the scope object. An unselected object cannot be inspected through the service layer.

### Masking before the model (✅)

`application/sample_masking.py:107` decorates `profile` so masking wraps the inner port — masking happens **before** any value can reach a prompt, trace or interception payload, satisfying §12's ordering requirement.

### Deductions

| Points | Reason |
|---:|---|
| −8 | `platform.*` namespace is a host package, not a published generic contracts artifact. Extraction requires renaming/repackaging across ~40 import sites. |
| −5 | `security.*` coupling in `api/drafts.py`, `api/analyses.py` binds the HTTP layer to the host's authorization model. |
| −3 | `ProposalKernel` is shared with returns governance; a standalone analyzer must either vendor it or split it into a released contract package. |
| −2 | `bootstrap/adapters/analyzer_*.py` (7 files) live outside the package; a standalone build must re-supply connector/persistence/AI adapters. This is expected wiring, but it is not yet packaged as a documented adapter SPI. |

**No analyzer business-logic rewrite is required for extraction.** The remaining work is packaging and contract publication, which is the healthiest possible position for this requirement.

---

# Section J — Graph / Sync Audit

| Aspect | Status | Evidence |
|---|---|---|
| Source binding versioned separately | ✅ | `api/source_bindings.py`, `configuration/domain/graph.py` |
| Graph schema as versioned artifact | ✅ | `api/schema_releases.py`, `config/dynamic_knowledge/active-schema.return-order.yaml` |
| Sync mapping separate | ✅ | `data_platform/mapping/{contracts,compiler,normalizer,projection}.py`, `config/data_platform/canonical_mappings.yaml` |
| Source read-only safety | ✅ | All INSERT/UPDATE/DELETE target `dbo.return_*`, `platform.*`, `integration.*`. No DDL/DML against source objects. |
| Analyzer scope enforcement | ✅ | `source_inspection.py:150` |
| Analyzer sample masking before AI | ✅ | `sample_masking.py:107` |
| Release lifecycle | ✅ | `ProposalStatus` full lifecycle; `schema_draft.py:75-123` enforces "any mutation invalidates VALIDATED" and "only VALIDATED can be approved" |
| Active schema immutable, re-analysis creates candidate | ✅ | `application/reanalysis_service.py`; `analyzer_schema_addition.py:19-20` refuses overwrite |
| **Change classification (additive / compatible / destructive)** | ❌ **MISSING (F-12)** | Only `ChangeKind` = ADDED/REMOVED/CHANGED at diff level. No class-driven activation strategy. `analyzer_schema_addition.py:157-158` simply **refuses** non-additive changes: *"replacing one is a destructive change and belongs on the migration path, not in an additive compile"* — but no migration path is implemented. |
| **Generation build → sync → validate → atomic swap → drain → retire** | ⚠️ **PARTIAL (F-11)** | `dynamic_knowledge/graph/generation.py` implements a full blue/green lifecycle with `GraphGenerationStatus`, fencing tokens and drain leases. But `data_platform/graph/sync_service.py:73-74` sets `_LEGACY_GENERATION_ID = LEGACY_GENERATION_ID` (`"legacy-live"`) and `_LEGACY_FENCING_TOKEN = 1`, and lines 422, 642 and 649 pin **every** node write, incremental sync and full sync to that single generation. Blue/green is built and not exercised. |
| Readers never observe a partial generation | ❌ | Follows from the above: a full sync mutates the live generation in place. |
| Full sync | ✅ | `sync_service.py:647` |
| Incremental sync | ✅ | `sync_service.py:640`, `recordScope: INCREMENTAL` |
| Durable watermark | ✅ | `capture_high_watermark` (line 281), `MongoSyncCheckpointStore` |
| Restart recovery / idempotent replay | ✅ | checkpoint store + `expected_generation_status=ACTIVE` fencing |
| Record-scoped on-demand sync | ✅ | `mode: "ON_DEMAND"` (line 220), `MongoTargetedSyncRunLedger`, wired into the order-agent runtime |
| Commit-before-read | ✅ | `return_case_activities.py:274-283` — the port's contract is explicitly post-commit |
| Run history with source/mode/watermark/counts/outcome/failure | ✅ | `GraphSyncRunView` (lines 115-141) |
| Incremental gaps surfaced, not silent | ✅ | `incremental_skipped_source_ids` (line 348) — sources lacking `incremental_cursor_field` are named rather than silently skipped |
| Permission-gated manual trigger | ✅ | `api/graph_sync.py` + `SyncControlPage.tsx` |
| Sync failure visible in UI, not "not found" | ✅ | failure reason carried on the run view and rendered |

---

# Section K — UI Inventory

| Screen | Route | Exists | Backend Connected | Functional | Missing Capability | UI Bugs | Documentation |
|---|---|---:|---:|---:|---|---|---|
| Landing Page | `/` | ✅ | ✅ | ✅ | Live status per card is limited to a static `NO BACKEND YET` badge; no live health | none found | Inline only |
| Returns Workspace | `/returns` | ✅ | ✅ | ⚠️ | Multi-RMA panel renders (`ReturnCopilotPage.tsx:891`) but is fed by a flow that never completes (F-1) | none found | Inline only |
| Support Console | `/support` | ✅ | ✅ | ⚠️ | No Bay recommendation panel (F-6); no tracking/shipment management (F-8); no correction flow; submit path broken by F-1 | none found | Inline only |
| Case Operations | `/operations` | ✅ | ⚠️ | ⚠️ | Self-declared `status: "NO BACKEND YET"` for generations/workers/outbox; no per-agent decision log, no waits/deadlines view, no permitted interventions | none found | Inline only |
| **Data Sources** | *(none)* | ❌ | — | ❌ | Not an independent screen — a section of `/config` (`CONFIG_SECTIONS` includes `"Data Sources"`). §22 forbids counting a nested tab (F-13) | — | Missing |
| Graph Schema Studio | `/graph-schema` | ✅ | ✅ | ✅ | Domain rail absent (`sections: []`); analyzer tabs are draft-scoped | none found | Inline only |
| Sync Control | `/sync` | ✅ | ✅ | ✅ | — | none found | Inline only |
| Configuration | `/config` | ✅ | ✅ | ✅ | Key/value + raw JSON editors present (`JsonView.tsx`); rollback surface not verified | none found | Inline only |
| AI Control Centre | `/ai` | ✅ | ✅ | ⚠️ | Interception queue shows only requests from the one path that can be intercepted (F-4) | none found | Inline only |
| **Approvals** | *(none)* | ❌ | — | ❌ | Entire screen missing despite complete backend (F-9) | — | Missing |

**Coverage: 8 of 10 required capabilities exist as independent screens.**

**Sidebar requirement (§22 final paragraph):** `registry.ts` gives contextual sections to only `/config` (10) and `/ai` (9). `/returns`, `/support`, `/graph-schema`, `/sync`, `/operations` all declare `sections: []`. The code comments justify this honestly ("a rail entry that routes nowhere is worse than an absent one"), but the requirement is unmet for 5 of 7 domains (F-16).

**Permission handling (§22 final paragraph): correct.** `registry.ts:11-16` documents that hiding is presentation-only and the backend refuses regardless; `useRailCollapsed.ts` and capability gating are implemented.

---

# Section L — Broken UI / UX

**No client-side UI defects were found.** This is a genuine strength.

| Check | Result |
|---|---|
| Vitest suite | 18 files / 168 tests, all pass |
| `tsc -b` | clean, exit 0 |
| Blank pages / runtime exceptions | none found; every domain page has a test file |
| API envelope consistency | enforced — `api/client.ts` rejects non-enveloped bodies; `api/noVersionedPaths.test.ts` pins the allowed versioned prefixes |
| Frozen-route calls from UI | enforced by `test_frozen_modules_gain_no_new_callers.py::test_no_frontend_module_calls_a_frozen_route` |
| Generated types current | `frontend/src/api/generated/return-platform.d.ts` present and typechecking against hand-written clients |

**The UI defects that exist are absence, not breakage:** F-9 (no Approvals screen), F-13 (Data Sources nested), F-16 (missing contextual rails), and the fact that the Returns and Support workspaces will render permanently empty states in production because of F-1.

One historical bug is documented as fixed and worth noting as a pattern: `api/order_agent.py:196-204` records that the turns route previously returned a bare body, which the browser client rejected outright — *"the failure looked like the agent, and was the response shape."* The contract test now prevents recurrence.

---

# Section M — Backend Gaps

| Gap | Type | Evidence |
|---|---|---|
| No caller starts `ReturnCaseWorkflow` | Dead runtime path | F-1 |
| No shipment/tracking create-update endpoint | Missing endpoint | F-8 |
| No bay recommendation call from the case workflow | Missing domain logic | F-6 |
| No SQL return-store write on the canonical RMA path | Missing persistence | F-7 |
| No business-calendar service | Missing domain logic | F-5 |
| No worker-side configuration refresh | Missing service | F-3 |
| No interception gate on `structured_invocation` | Missing validation/control | F-4 |
| No schema change classifier or destructive migration path | Missing domain logic | F-12 |
| Graph generation swap never exercised (`legacy-live` pinned) | Dead runtime path | F-11 |
| `failure_policy` never read at runtime | Dead configuration | F-14 |
| `BayResultNotice` lacks `return_location` and `confidence` | Incomplete contract | F-6 |
| `/api/v1/associate-returns/*` and `/api/v1/return-agents/*` mounted but frozen | Dead HTTP surface (deliberate) | `api/associate_returns.py:43-50` |

**Error classification (§24):** correct. `OrderAgentFailure` carries an explicit `retryable` flag; `_BEST_EFFORT_RETRY` vs `_PERSIST_RETRY` vs `_DRAFT_RETRY` are distinguished; `_classify_pymongo_error` and `_classify_async_probe_error` separate transient from permanent. No infinite-retry-of-permanent-failure pattern was found.

---

# Section N — Configuration Gaps

| Gap | Evidence |
|---|---|
| Identification fields, operators, entity bindings and per-field limits hardcoded | `search_strategy.py:151-263` (F-10) |
| `_SIGNATURE_FIELDS`, `_UNSUPPORTED_INTENT_FIELDS`, `_DATE_FIELD_ID` hardcoded | `search_strategy.py:50,77-78,102-120` |
| `MAX_CACHED_CANDIDATES`, `RESULT_PAGE_SIZE`, `FUZZY_CUSTOMER_PROBE_LIMIT`, `FUZZY_CUSTOMER_MATCH_THRESHOLD` are module constants, not configuration | `search_strategy.py:86-96` |
| `discovery.*` and `clarification_policy.*` config bound only to the frozen path | grep: consumed by `associate_flow.py`, `agents/order_discovery.py`, `agents/order_analysis.py` |
| `business_calendar_id` / `timezone` stored and never read | F-5 |
| `failure_policy` stored and never read | F-14 |
| No worker propagation for any runtime configuration family | F-3 |
| `ORDER_DISCOVERY_TASK_QUEUE` / `RETURN_WORKFLOW_TASK_QUEUE` are `Final` module constants | `order_discovery_worker.py:14`, `worker.py:19` — overridable per call, not by configuration |

**Correct by design (not gaps):** `_RESTART_REQUIRED_SETTINGS` fails closed when a release attempts to change infrastructure endpoints (`runtime_activation.py:180-190`); packaged YAML is never rewritten at runtime; secrets resolve only from Vault.

---

# Section O — Security Findings

**No exploitable vulnerability was found.** The security posture is the second-strongest area after AI privacy.

| Area | Status | Evidence |
|---|---|---|
| Tenant isolation | ✅ | `api/cases.py:89-96` — `_belongs_to` requires tenant **and** principal, with a comment explaining that "either" would leak across a repeated principal id. `conversation_repository.py:55-60` scopes on `{tenantId, principalId}`. |
| IDOR / cross-tenant case access | ✅ | Adversarial #14 addressed: a guessed case id fails the tenant+principal predicate. |
| Cross-tenant candidate confirmation | ✅ | `CandidateSet.validate_selection` re-checks conversation, principal, tenant and graph generation. |
| Role checks | ✅ | `require_read_roles`, `require_write_roles`, `require_support_roles`, `require_associate_roles`, `require_warehouse_roles`, `require_capability`. RMA issuance is support-gated (`return_support.py:326`). |
| SQL injection | ✅ | `ai_studio.py:1161-1163` validates every identifier against `_SAFE_IDENTIFIER` before interpolation and parameterizes all values. `sql_business_state.py:283-294` interpolates only hardcoded literal table/column names from call sites, values parameterized. |
| Source mutation via AI Studio | ✅ **strong** | `ai_studio.py:1142-1158` raises `PermissionError` unless a dedicated sandbox host, user **and** database are configured, and raises again if any of them equals the production value. |
| Cypher injection | ✅ | Compiled plans with parameterized values; `_quoted` for identifiers; `schema_guard` + `query_safety_guard` validate every plan before compilation. |
| Arbitrary query exposure | ✅ | Connector contract has no arbitrary-query method. |
| Analyzer scope escape | ✅ | Programmatic scope enforcement at the service layer. |
| Credential leakage to agents/prompts/browser | ✅ | Agents receive logical source ids; Vault resolution happens in trusted adapters; `settings.py` uses `SecretStr`. |
| PII leakage to providers | ✅ | Recursive redaction including JSON-in-string (§19). |
| PII at rest in interception store | ✅ | `SystemStoreInterceptionStore` seals payloads via `EnvelopeEncryptor` (`store.py:91-108`). |
| Prompt injection | ✅ | `ai/safety/injection_guard.py`, `inspect_input`/`inspect_output`, `scope_guard.py`. |
| Custom prompt injection in production | ✅ | Blocked at `service.py:383-387` and `api/ai_gateway.py:348-352`. |
| Human-in-the-loop provider in production | ✅ | Hard-gated to dev/test. |
| Unsafe logs | ✅ | Log `extra=` payloads carry ids, counts and error types — no values sampled. |
| CORS | ✅ | `PLATFORM_FRONTEND_CORS_ORIGIN` explicit, not wildcard. |
| Secrets in repo | ⚠️ P4 | `.env`, `.env.vault-backup` exist on disk; commit `0615921` untracked them and `.gitignore` now covers every `.env` variant. Residual risk is historical git history only — **not verified in this audit** (`NOT PROVABLE`). |

**Recommendation:** run a history scan (`git log -p -- .env.vault-backup`) to confirm no secret was ever committed before `0615921`.

---

# Section P — Performance Findings

| # | Component | Adversarial workload | Root cause | Evidence | Correct optimization direction |
|---|---|---|---|---|---|
| P-1 | Order Discovery fuzzy fallback | 250k customers, misspelled name | Unfiltered unordered `LIMIT 100` scan + O(n·m) client-side `difflib` windowing | `search_strategy.py:466-513` | Route through the existing `customer_name_search_v2` full-text index with a fuzzy term (`name~`), bound by score not row count. **This is a correctness fix (F-2), not merely a speed one.** |
| P-2 | Progressive plan fan-out | Associate supplies 6 signals in one message | One graph round-trip per signal per turn, serially (`for plan in plans:` at `graph_nodes.py:634`) | same | Batch independent reads into a single Cypher with `UNION`, or execute the plan set concurrently with `asyncio.gather` under the existing query budget. |
| P-3 | Runtime config refresh on the request path | High RPS | `activator.refresh()` runs inside request middleware on every request (`main.py:1031-1033`) | — | Mitigated by the 5-second monotonic poll guard and double-checked lock (`runtime_activation.py:76-95`) — **acceptable as implemented**; noted only because a `get_head_revision()` round-trip still occurs every 5s per process. Move to a change-stream/pub-sub watch to remove polling entirely. |
| P-4 | Graph full sync | Production order volume | Full sync writes in place to `legacy-live` rather than building a new generation | `sync_service.py:649` (F-11) | Build into a new generation and swap; also removes the partial-read window. |
| P-5 | Bay wait dead time | Every case | 120s of critical-path dead time waiting for a signal that is never sent | `return_case_workflow.py:436` (F-6) | Fix F-6; the 120s default is documented as deliberate and "measure before raising it", which is sound once a real result arrives. |
| P-6 | Repeated invariant prompt content | Every agent turn | Not verified — prompt caching / invariant-prefix optimization was not located | — | `NOT PROVABLE`. If absent, adopt provider prompt caching for the invariant schema prefix, which dominates `contextJson`. |
| P-7 | Connection pooling | Sustained load | `pymssql.connect(...)` opens a new connection per operation in `sql_business_state.py` and `ai_studio.py` | `sql_business_state.py:53-64` | Introduce a pooled SQL connection manager. Low current impact because these paths are legacy/seed-only. |

**Not found (good):** no N+1 conversational search (pagination uses a cached candidate set), no unbounded `collect()`, no repeated DB config lookup per request beyond the guarded refresh, no retry storms (bounded attempts with a global deadline).

---

# Section Q — Dead / Duplicate / Legacy Code

The repository governs its duplication deliberately and well. `backend/tests/test_frozen_modules_gain_no_new_callers.py` pins the **exact** allowed importer set for every frozen module and fails on both addition and silent removal. Its docstring states the position plainly: *"Two complete Order Discovery implementations exist in this codebase, and the console reaches only one of them."*

| Concern | Canonical runtime | Competing / dead | Status |
|---|---|---|---|
| Order Discovery | `dynamic_knowledge/order_agent/*` behind `/api/v2/order-agent` | `operations/associate_flow.py` (138 KB — the largest file in the repo) + `api/associate_returns.py` behind `/api/v1/associate-returns` | FROZEN, mounted, `deprecated=True`, allowlist-enforced |
| Order Discovery agent | `dynamic_knowledge/order_agent` | `agents/order_discovery.py` (+ `.md`) | FROZEN, imported only by `agents/registry/registry.py` |
| Return agents HTTP | Temporal workflows + `/api/v2/order-agent` | `api/return_agents.py` behind `/api/v1/return-agents` | FROZEN |
| Active schema | `config/dynamic_knowledge/active-schema.return-order.yaml` via `load_active_schema` | `data_platform/graph/interim_active_schema.py` (36 KB) | FROZEN, **zero importers** — fully dead |
| AI package | `return_platform.ai` | `return_platform.ai_gateway` (6 files, all < 1 KB) | Pure re-export shim; ~20 modules still import it including `main.py` |
| Case workflow | *(none — nothing starts it)* | `workflows/return_case_workflow.py` | **Effectively dead at runtime (F-1)** |
| Graph generation lifecycle | `legacy-live` single generation | `dynamic_knowledge/graph/generation.py` blue/green machinery | Built, unexercised (F-11) |
| Bay recommendation | *(none on case path)* | `operations/warehouse/service.py` + `/api/v1/warehouse/returns/.../bay-recommendation` | Reachable only by direct session-keyed API call (F-6) |
| SQL return persistence | *(none on case path)* | `sql_business_state.persist_support_result` | Legacy session path + seed only (F-7) |

**The critical inversion:** in three separate cases the frozen implementation is *more* capable than the canonical one — full-text fuzzy search (F-2), configuration-driven field catalogue (F-10), and pre-dispatch AI interception (F-4). Consolidation redirected traffic before parity was reached. Deleting the frozen modules today would remove capability, not just code.

**Also present:** `fix_eslint.py` and `fix_imports.py` at repository root (one-off migration scripts), `backend.log` / `worker.log` (committed logs), `backend/validation_output_new.txt`, and 27 documents under `docs/` including superseded `STAGE_4L`/`STAGE_4M` plans that compete with the current unified plan as apparent truth (§30.1 forbids this).

---

# Section R — Test Coverage Gaps

**Inventory:** 2,793 backend tests collected with zero collection errors; 294 test files across `agents`, `api`, `bootstrap`, `configuration`, `conversation`, `data_platform`, `dynamic_knowledge`, `graph_schema_analyzer`, `operations`, `platform`, `reasoning`, `security`, `source_connectors`, `v2`. Frontend: 18 files / 168 tests, all passing. Playwright configured (`playwright.config.ts`) for browser coverage.

**Classification:** unit, integration, contract (`noVersionedPaths.test.ts`, frozen-module architecture tests), real-infrastructure (`*_real_infra.py`), workflow replay (`test_order_discovery_workflow.py`), and security tests are all present. This is a serious test suite.

### Mandatory adversarial scenarios (§29)

| # | Scenario | Covered | Evidence / Gap |
|---:|---|---|---|
| 1 | Simultaneous confirmations | ✅ | Idempotency key on `tenant\|conversation\|order\|line-set` |
| 2 | Duplicate confirmation | ✅ | same |
| 3 | Duplicate activity | ✅ | derived `fact_id`, `create_return_record` unique index |
| 4 | Duplicate signal | ⚠️ | Handled in workflow by design; not provable (F-1) |
| 5 | Duplicate support response | ⚠️ | documented at `return_support.py:336-339`; unreachable |
| 6 | Out-of-order support response | ❌ | no test |
| 7 | Bay result before wait | ✅ | `test_return_case_workflow_real_infra.py:304` |
| 8 | Bay never returns | ✅ | timeout branch tested |
| 9 | Bay crash | ✅ | `ActivityError` branch |
| 10 | Restart during support wait | ⚠️ | `continue_as_new` implemented; restart test not located |
| 11 | Concurrent fact writers | ⚠️ | `expected_version` exists; no concurrency test located |
| 12 | Config activated mid-case | ⚠️ | `STRICT_PINNING` implemented; **no worker-side test (F-3)** |
| 13 | Graph generation changed mid-case | ⚠️ | generation binding exists; single generation in practice (F-11) |
| 14 | Guessed cross-tenant case/conversation id | ✅ | `_belongs_to` |
| 15 | One RMA with two items | ✅ | nested model |
| 16 | Two RMAs for one order | ✅ | `records` tuple |
| 17 | Wrong label associated with RMA | ✅ | structurally impossible |
| 18 | Return created but graph sync fails | ✅ | `synchronize_return_records` raises when no port |
| 19 | Warehouse sync fails | ⚠️ | best-effort path exists |
| 20 | Shipment appears after fulfilment starts | ❌ | no shipment write path exists (F-8) |
| 21 | Relative-date replay next day | ✅ | `contracts.py:252-258` — date-bearing questions folded into the turn idempotency key |
| 22 | **Misspelled customer outside bounded window** | ❌ | **the exact failure of F-2, and the test suite asserts the bound as correct** (`test_search_strategy.py:214`) |
| 23 | Analyzer reads unselected object | ⚠️ | scope enforced; explicit test not located |
| 24 | Analyzer attempts mutation | ✅ | no mutation method on the contract |
| 25 | Draft config activation | ✅ | `schema_draft.py:110-118` |
| 26 | Unauthorized approval | ✅ | `require_capability` |
| 27 | Nested serialized PII | ✅ | `redaction.py` — well tested |
| 28 | Missing model pricing | ✅ | `pricing.py` UNKNOWN |
| 29 | Runtime model change without API restart | ✅ | `replace_routes` |
| 30 | **Runtime model change without worker restart** | ❌ | **F-3 — no test, and it would fail** |
| 31 | **Runtime config change without worker restart** | ❌ | **F-3 — no test, and it would fail** |
| 32 | Structured AI invocation intercepted before dispatch | ❌ | **F-4 — no test, and it would fail** |
| 33 | Duplicate RMA form submit | ⚠️ | workflow-side; unreachable |
| 34 | Out-of-order tracking update | ❌ | no tracking write path (F-8) |
| 35 | Partial name treated as confirmed | ✅ | fuzzy candidates carry `customer_name_fuzzy` marker |
| 36 | Initial return reason asked again | ❌ | no test located |
| 37 | **Tenth discovery field without source change** | ❌ | **F-10 — no test, and it would fail** |

**Coverage: 17 of 37 clearly satisfied, 10 partial/unreachable, 10 absent.**

### False-confidence tests identified

1. **`backend/tests/configuration/test_agent_failure_policy.py`** — loads `production.yaml`, builds `AgentDescriptor`, asserts `descriptor.failure_policy == <the value from that same YAML>`. It crosses no integration boundary and proves only that a Pydantic field round-trips. Its own docstring claims the policy "was unenforceable by construction" and implies the descriptor fixed it; nothing reads the field at runtime (F-14).
2. **`backend/tests/dynamic_knowledge/test_search_strategy.py:205-215`** — `test_...probe_plan` asserts `limit == FUZZY_CUSTOMER_PROBE_LIMIT`. It pins the P0 defect as intended behaviour.
3. **`backend/tests/dynamic_knowledge/test_search_strategy.py:177-204, 394`** — `fuzzy_match_customers` is tested against hand-written row lists of 3–5 rows. No dataset is large enough to expose the bounded-window failure (§29's explicit warning).
4. **`backend/tests/test_return_case_workflow_real_infra.py`** — high-quality tests of a workflow that no production caller starts. The suite proves the component and never the connection (F-1).

---

# Section S — Documentation Defect Inventory

| ID | Level | File / Symbol / Screen | Current Documentation Problem | Actual Behavior | Correct Documentation Required | Severity |
|---|---|---|---|---|---|---|
| D-1 | Function | `dynamic_knowledge/order_agent/graph_nodes.py:1023-1026` `confirm_order` | States *"`ReturnCaseWorkflow` does not exist yet"* and that the workflow "binds to it when it lands" | The workflow exists at `workflows/return_case_workflow.py:314`; nothing starts it | See T-1 | **P1** |
| D-2 | Repository | `README.md` §Ownership boundaries | Table says **SQL Server … Read-only** | `sql_business_state.py` INSERTs into `dbo.return_requests`, `dbo.return_items`, `dbo.return_fulfillment`, `dbo.return_tracking`, `integration.return_support_ticket` | See T-2 | **P1** (security-boundary table) |
| D-3 | Class | `configuration/return_configuration.py:296-298` `ReturnCaseTimingConfiguration` | *"Support durations are business-calendar durations… `business_calendar_id` names the calendar that decides"* | Pure wall-clock; neither field is ever read | See T-3 | **P1** |
| D-4 | Module | `dynamic_knowledge/order_agent/search_strategy.py:91-95` | Justifies the 100-row probe with *"Neo4j has no built-in edit-distance function (that's an APOC extension, not installed here), so a misspelled name can't be resolved with a single server-side query"* | A full-text index (`customer_name_search_v2`) exists, is bootstrap-verified, and is queried by `associate_flow.py:1385` — no APOC needed | See T-4 | **P1** |
| D-5 | Repository | `README.md` §Current architecture | *"Four canonical domains: /returns /config /graph-schema /ai"* | Seven domains exist: `+/support`, `+/sync`, `+/operations` (`registry.ts:97-195`) | See T-5 | P2 |
| D-6 | Repository | `README.md` consolidation banner | Names target tree `{bootstrap,platform,configuration,agents,business,graph,graph_schema_analyzer,ai}` | No `business` or top-level `graph` package exists; actual tree adds `operations`, `dynamic_knowledge`, `data_platform`, `workflows`, `canonical`, `conversation`, `security`, `validation`, `workers` | See T-5 | P2 |
| D-7 | Class | `agents/contracts/descriptor.py:32-36` | *"Until this existed the policy was documented and unenforceable"* — implies the descriptor makes it enforceable | Nothing reads `failure_policy`; behaviour is hardcoded per workflow | See T-6 | P2 |
| D-8 | Module | `data_platform/graph/sync_service.py:73-74` | `_LEGACY_GENERATION_ID` / `_LEGACY_FENCING_TOKEN` carry no explanation of why blue/green is bypassed or when it will be adopted | Every sync writes one generation in place | See T-7 | P2 |
| D-9 | Screen | all ten screens | No functional screen documentation exists anywhere (no `docs/screens/`, no per-screen doc) | Screens documented only by inline TSX comments | See Section U + T-8 | P2 |
| D-10 | Optimization | repository-wide | No optimization documentation exists for indexed search, candidate narrowing, config caching, generation swap, incremental sync, batching, retry/backoff, pooling, model routing | Optimizations exist undocumented; one documented optimization (full-text index) is unused on the canonical path | See Section V + T-9 | P2 |
| D-11 | API | `openapi.json` | 126 paths generated; no per-endpoint documentation of caller role, idempotency, concurrency, side effects, configuration-release behaviour or audit behaviour | Only FastAPI docstrings, of mixed depth | See T-10 | P2 |
| D-12 | Config | `config/README.md` (6.4 KB) | Does not document per-family security classification, bootstrap-only vs runtime-editable, hot-change support, propagation behaviour, in-flight case behaviour or rollback implications | Configuration families are undocumented in these dimensions | See T-11 | P2 |
| D-13 | Repository | `docs/STAGE_4L_*.md`, `docs/STAGE_4M_*.md`, `docs/FINAL_ORDER_DISCOVERY_*`, `docs/ORDER_DISCOVERY_*` (12+ files) | Superseded plans and designs sit beside current docs as competing truth — explicitly forbidden by §30.1 | Current truth is `docs/UNIFIED_RETURN_PLATFORM_*` | Delete or move under `docs/archive/` with a header stating supersession | P3 |
| D-14 | Repository | `README.md` | Mojibake: `â€"` for em-dash, `Â§` for section sign | Encoding corruption in a UTF-8 file | Re-encode | P4 |
| D-15 | Repository | `README-back.md`, `PACKAGE_MANIFEST.md`, `AGENTS.md` | Three additional root-level docs of unclear currency; `README-back.md` is an apparent backup | — | Delete `README-back.md`; date-stamp the others | P4 |

---

# Section T — Correct Documentation Content

### T-1 — Replacement for `confirm_order` docstring (`graph_nodes.py:1002`)

```
"""Turn a searched-for order into a durable case and start its workflow.

This is the transition from "the associate has chosen" to "the platform is
working the return". Two things make the confirmation trustworthy rather
than a claim:

* The selection is validated against the live `CandidateSet` -- the same
  guard `graph_query` uses -- so a model cannot confirm an order it did not
  find, one belonging to a different conversation or principal, or one from
  an expired or superseded graph generation.
* The case store is idempotent on `tenant | conversation | order |
  line-set`, so a Temporal retry of the same turn, or two confirmations
  racing, yield one case.

**This node also starts `ReturnCaseWorkflow`.** Case creation and workflow
start are one logical step: a case that exists without its workflow is
invisible to Support, accrues no reminders, and cannot receive an RMA -- the
signal in `api/return_support.py` targets `return_case_workflow_id(case_id)`
and fails with NOT_FOUND if nothing started it.

The start is idempotent by workflow id (`return_case_workflow_id(case_id)`);
`WorkflowAlreadyStartedError` is caught and treated as success, so a retried
turn attaches to the running case rather than raising or starting a second.

Failure to start the workflow fails the turn. Order Discovery is `blocking`
by declared policy, and a case with no workflow is worse than a turn the
associate can retry.
"""
```

### T-2 — Replacement for the README ownership table row

```markdown
| SQL Server | Two distinct roles. `dbo.return_requests`, `dbo.return_items`,
`dbo.return_fulfillment`, `dbo.return_tracking` and
`integration.return_support_ticket` are **platform-owned** return/RMA/tracking
state: the platform creates and updates them through
`operations/sql_business_state.py` and owns their migrations
(`configuration/sql_migrations/`). Every **other** schema and table reachable on
this server is source-owned business data and is **read-only** to the platform:
no INSERT, UPDATE, DELETE or DDL is issued against it from the API, agents,
Graph Analyzer, UI or sync. | Read/write on platform-owned schemas; read-only
on all source-owned objects |
```

Add immediately beneath the table:

```markdown
**How this boundary is enforced.** `data_platform/ai_studio.py` refuses to write
unless a dedicated sandbox host, user *and* database are configured and all three
differ from the production connection (`_apply_sql`, `PermissionError`), and
validates every identifier against `_SAFE_IDENTIFIER` before interpolation.
Source connectors expose scan and point-lookup only — the contract in
`source_connectors/protocols.py` has no arbitrary-query and no mutation method.
```

### T-3 — Replacement for `ReturnCaseTimingConfiguration` docstring

Use this **once business-time is implemented**:

```
"""How long the case waits, and how often it chases.

Defaults, not constants: every field is editable through a configuration
release. A workflow reads them once at start and keeps them for its own
lifetime -- an in-flight return must not have its deadline moved underneath
it -- so a change applies to new cases only.

Support durations are business-calendar durations. Eight hours means eight
*working* hours: `business_calendar_id` selects the working-day/holiday
calendar and `timezone` is the IANA zone those hours are measured in, so a
wait that starts Friday afternoon resumes Monday morning rather than
expiring overnight. `BusinessCalendar.advance(start, duration, calendar_id,
timezone)` performs the arithmetic; the workflow calls it for both the
overall deadline and each reminder interval.
"""
```

Until it is implemented, the docstring **must** instead say:

```
"""How long the case waits, and how often it chases.

Defaults, not constants: every field is editable through a configuration
release. A workflow reads them once at start and keeps them for its own
lifetime, so a change applies to new cases only.

**Durations are wall-clock, not business-calendar.** `business_calendar_id`
and `timezone` are carried on the release and on `ReturnCaseTimings` but are
not yet read by any timer calculation (see `return_case_workflow.py`
`_await_support`). A Friday-afternoon return therefore reminds and exhausts
overnight and over weekends. Business-calendar arithmetic is tracked as a
known gap; do not configure these values expecting them to take effect.
"""
```

### T-4 — Replacement for the fuzzy-probe module comment (`search_strategy.py:91-95`)

```python
# Misspelled customer names are resolved through the Neo4j full-text index
# `customer_name_search_v2` (created by migration 0013 and verified ONLINE at
# bootstrap by `apply_neo4j_migrations.py`). A full-text query with a fuzzy
# term searches the *complete* customer set server-side and returns matches
# ranked by score, so the correct customer cannot fall outside a client-side
# window.
#
# This does not require APOC. An earlier implementation fetched an unfiltered
# batch of rows and scored them with `difflib` on the assumption that Neo4j had
# no server-side approximate match; that bounded the search to an arbitrary,
# unordered subset and could silently miss the correct order at production
# scale. The index name is configuration (`progressive.customer_fulltext_index`),
# not a constant, so an operator can repoint it without a code change.
FUZZY_CUSTOMER_MIN_SCORE = 0.72   # index score floor, not a row bound
```

### T-5 — Replacement README architecture section

```markdown
## Current architecture

Seven console domains, each with its own route, backend surface and capability gate
(`frontend/src/domains/registry.ts` is the single source of truth):

| Domain | Route | Purpose | Capability |
|---|---|---|---|
| Return Business Copilot | `/returns` | Take a customer from a partial description to a completed return | `returns.session.read` |
| Returns Support | `/support` | Answer the agent's return requests; issue RMA, label and pickup | `returns.session.read` |
| Configuration | `/config` | Change platform behaviour and release it safely | `config.runtime.read` |
| Graph Schema Analyzer | `/graph-schema` | Turn source collections into the graph the copilot searches | `graph_schema.draft.read` |
| AI Control Center | `/ai` | See what the models were asked; answer anything held for review | `ai.request.read` |
| Source Sync | `/sync` | Check the graph is current; rebuild it from sources | `config.source.read` |
| Operations | `/operations` | Work the return queues; see platform health | `config.runtime.read` |

Two capabilities required by the platform specification have **no screen yet**:
**Approvals** (backend complete at `/api/.../proposals`; no UI) and **Data Sources**
as an independent screen (currently a section of `/config`).

The backend package tree is:

    backend/src/return_platform/
      ai/  agents/  api/  bootstrap/  canonical/  configuration/  conversation/
      data_governance/  data_platform/  dependency_simulation/  dynamic_knowledge/
      graph_schema_analyzer/  operations/  platform/  secrets/  security/  shared/
      source_connectors/  validation/  workers/  workflows/
```

Delete the consolidation banner's `{bootstrap,platform,configuration,agents,business,graph,graph_schema_analyzer,ai}` line — no `business` or top-level `graph` package exists.

### T-6 — Replacement for `AgentDescriptor.failure_policy` comment

```python
    # Declared policy. `blocking` means an agent failure should park the case
    # and wait for recovery; `best_effort` means it should be recorded and the
    # return should continue. Order Discovery and Return Workflow are blocking;
    # Order Analysis, Bay, Fulfillment and Feedback are not.
    #
    # NOTE: this field is currently *declaration only*. No runtime component
    # reads it. Actual behaviour is hardcoded per workflow -- see
    # `return_case_workflow.py`, where the bay activity uses `_BEST_EFFORT_RETRY`
    # and catches `ActivityError`, while `synchronize_return_records` raises.
    # Changing this value in `config/returns/production.yaml` therefore has no
    # runtime effect. `tests/configuration/test_agent_failure_policy.py` pins the
    # declaration only and must not be read as evidence of enforcement.
    failure_policy: Literal["blocking", "best_effort"] = "blocking"
```

### T-7 — Documentation required at `sync_service.py:73`

```python
# Every sync in this service writes into a single, permanently-active graph
# generation rather than building a new one and swapping.
#
# The blue/green machinery exists and is complete (`dynamic_knowledge/graph/
# generation.py`: GraphGeneration, fencing tokens, read/write drain leases,
# ProjectionOwnership). It is not used here. The consequences of that, stated
# plainly so nobody has to rediscover them:
#
#   * A full sync mutates the live graph in place, so a reader can observe a
#     partially rebuilt graph while it runs.
#   * A destructive schema change has no safe cutover path; `analyzer_schema_
#     addition.py` refuses non-additive changes instead of migrating them.
#   * The fencing token is constant, so it fences nothing.
#
# Adopting generations here means: allocate a generation, sync into it, validate,
# swap the ActiveRuntimeSnapshot, drain readers on the old generation, retire it.
_LEGACY_GENERATION_ID = LEGACY_GENERATION_ID
_LEGACY_FENCING_TOKEN = 1
```

### T-8 — Required screen documentation template (`docs/screens/<domain>.md`)

```markdown
# <Screen name>

**Route:** `/<path>` (sections: `/<path>/<slug>`)
**Purpose:** <one line: what a user comes here to accomplish>
**Roles:** <which actors use it>
**Required capability:** `<capability>` — gates *visibility* only; the backend
authorizes every action independently.

## UI regions
<region> — <what it shows, from which API>

## Actions
| Action | Endpoint | Method | Capability | Idempotent | Audited |
|---|---|---|---|---|---|

## Backend APIs consumed
| Endpoint | Purpose | Envelope | Error codes surfaced |
|---|---|---|---|

## Live-state behaviour
<polling / SSE / manual refresh; what goes stale and when>

## Loading, error and empty states
| State | Trigger | Rendered as |
|---|---|---|

## Persistence and data source
<which store is authoritative for what this screen shows>

## Audit effects
<what a user action writes to the audit log>

## Configuration dependencies
<which runtime configuration changes this screen's behaviour, and whether
adoption is immediate>

## Known constraints
<what this screen deliberately does not do>
```

### T-9 — Required optimization documentation (`docs/optimization/order-discovery-search.md`)

```markdown
# Order Discovery search strategy

## Problem
An associate supplies partial, misspelled or approximate identifying information.
The correct order must be found from the complete dataset, not a sample.

## Scale assumption
Customers: 10^5–10^6. Orders: 10^6–10^7. Order lines: 10^7+.
A strategy that is correct at seed scale (10^3) and wrong at production scale is
a correctness defect, not a performance one.

## Strategy
1. **Exact anchors first.** Order number / order id → `EXACT`, `LIMIT 1`,
   backed by a uniqueness constraint. Cheapest and most selective.
2. **Indexed narrowing.** SKU, email, phone, postal code, state → `EXACT` against
   indexed properties. City, street, product description → `CONTAINS`.
3. **Server-side approximate match.** Customer name and product description
   resolve through the Neo4j full-text indexes `customer_name_search_v2` and
   `product_description_search_v2` (migration 0013). Fuzzy terms are evaluated
   inside the index across the complete set and returned ranked by score.
4. **Candidate narrowing, not enumeration.** Each signal produces an
   independently-scoped plan; `rank_search_results` merges and scores. At most
   `MAX_CACHED_CANDIDATES` are retained and `RESULT_PAGE_SIZE` shown per turn.

## Why it is safe
No stage bounds the *search space* — only the *result set*, after ranking.
A correct order cannot be excluded by a row cap applied before scoring.

## Indexes required
| Index | Type | Object | Created by | Verified by |
|---|---|---|---|---|
| `customer_name_search_v2` | FULLTEXT | `Customer.customer_name` | migration 0013 | `apply_neo4j_migrations.py` |
| `product_description_search_v2` | FULLTEXT | `Product.product_description` | migration 0013 | `apply_neo4j_migrations.py` |

Bootstrap **fails closed** if either index is missing or not ONLINE.

## Caching and invalidation
Candidate sets are cached per conversation for 30 minutes, keyed by
`search_intent_signature`. A new intent invalidates the page cursor. Expiry is
enforced in `CandidateSet.validate_selection`, so a stale set cannot be confirmed.

## Consistency tradeoff
Reads are served from the active graph generation, which lags source by at most
one sync interval. An agent may trigger a record-scoped on-demand sync and read
after it commits.

## Fallback
If a full-text index is unavailable the search degrades to exact + CONTAINS and
**reports reduced recall to the caller** via `unsupported_signals`. It must never
silently substitute a bounded client-side scan.

## Limits
`max_graph_queries_per_turn` (agent policy) bounds total round-trips per turn.

## Observability
`order_search_fulltext_matched`, `order_search_plan_rejected`,
`order_search_zero_results`; every search writes `QueryEvidence` with the plan and
compiled-query checksums.

## Failure mode
Index missing → bootstrap fails. Index stale → recall drops without error; monitor
zero-result rate against the confirmation rate.
```

Equivalent documents are required for: config caching/invalidation, graph
generation swap, incremental sync watermarks, prompt/invariant-prefix caching,
model routing and cost optimization, batching, retry/backoff, connection pooling.

### T-10 — Required API documentation block (per endpoint)

```markdown
### POST /api/v1/return-support/work-items/{work_item_id}/return-outcome

**Purpose:** Deliver Support's answer — one or more RMAs — to the case that requested it.
**Caller role:** Returns Support Operator.
**Authorization:** `require_support_roles`. Issuing an RMA is Support's act.
**Request:** `ReturnOutcomeRequest` — `records[]` (`returnReference`, `trackingReference?`,
`labelReference?`, `returnLocation?`, `shippingInstructionReference?`,
`orderLineReferences[]`), `rejected`, `reason?`. Max 100 records.
**Response:** `{ data: { caseId }, meta: { request_id } }`.
**Errors:** `404 WORK_ITEM_NOT_FOUND`; `409 WORK_ITEM_HAS_NO_CASE`;
`503 WORKFLOW_HOST_UNAVAILABLE`.
**Side effects:** Signals `support_response` on `ReturnCaseWorkflow`. This endpoint
writes nothing itself — the workflow records the return records, moves case status,
stops the reminder cadence and triggers graph sync.
**Idempotency:** Duplicate sends are absorbed by the workflow, not deduplicated here.
Pressing send twice yields one set of return records.
**Concurrency:** Signals are ordered by Temporal per workflow; the workflow ignores a
second response.
**Configuration-release behaviour:** None — the case's timings were pinned at start.
**Audit:** The workflow's activities append case facts with
`channel=CHANNEL_B, source_system=RETURN_SUPPORT`.
**Precondition:** The case's `ReturnCaseWorkflow` must be running. If it was never
started this call fails; see F-1.
```

### T-11 — Required configuration documentation (per family)

```markdown
### `return_case` — case timing

| Key | Type | Default | Range | Classification | Bootstrap-only | Hot change | Adoption |
|---|---|---|---|---|---|---|---|
| `bay_wait_seconds` | int | 120 | 0–86400 | Business | No | Yes | New cases only |
| `support_response_wait_seconds` | int | 28800 | ≥60 | Business | No | Yes | New cases only |
| `reminder_interval_seconds` | int | 7200 | ≥60 | Business | No | Yes | New cases only |
| `max_reminders` | int | 3 | 0–50 | Business | No | Yes | New cases only |
| `on_reminders_exhausted` | enum | `PARK_FOR_OPERATIONS` | `PARK_FOR_OPERATIONS\|ESCALATE` | Business | No | Yes | New cases only |
| `business_calendar_id` | str | `default` | — | Business | No | **Not implemented** | **No effect** |
| `timezone` | str | `UTC` | IANA | Business | No | **Not implemented** | **No effect** |

**Propagation:** A published release is adopted by the API process within 5 seconds
(`RuntimeConfigurationActivator`, poll-guarded) or immediately on forced activation.
**Worker adoption: none.** Worker processes read configuration once at startup and
must be restarted (see F-3).
**In-flight cases:** pinned to the release they started under; a running case never
observes new timings.
**Validation:** Pydantic bounds above; a release failing validation is refused at
activation, and the process keeps its last good snapshot.
**Rollback:** Re-activate the prior release. In-flight cases are unaffected; new
cases pick up the rolled-back values.
```

---

# Section U — Screen Documentation Matrix

| Screen | Functional Doc Exists | Correct | Missing Sections | Required Correct Documentation |
|---|---:|---:|---|---|
| Landing Page | ❌ | — | all | T-8 template; document the card status model and what "NO BACKEND YET" means |
| Returns Workspace | ❌ | — | all | T-8; must document that RMA/tracking panels populate only via `ReturnCaseWorkflow` (F-1) |
| Support Console | ❌ | — | all | T-8; must document the signal-not-write model, the F-1 precondition, and absent bay panel |
| Case Operations | ❌ | — | all | T-8; must document which half is backed and which is `NO BACKEND YET` |
| Data Sources | ❌ | — | all | T-8 **plus** an implementation gap entry — §22 requires an independent screen (F-13) |
| Graph Schema Studio | ❌ | — | all | T-8; document draft-scoped tabs vs domain sections |
| Sync Control | ❌ | — | all | T-8; document mode/recordScope/watermark semantics and skipped-source reporting |
| Configuration | ❌ | — | all | T-8; document key/value ↔ raw JSON consistency, versions, diff, rollback, activation |
| AI Control Centre | ❌ | — | all | T-8; must state which invocation paths can be intercepted (F-4) |
| Approvals | ❌ | — | all | T-8 **plus** an implementation gap entry — screen does not exist (F-9) |

**No screen documentation exists in this repository.** There is no `docs/screens/` directory and no per-domain functional document; screens are documented only by inline TSX comments, which are frequently excellent but are not the functional documentation §30.6 requires.

---

# Section V — Optimization Documentation Matrix

| Optimization | Implemented | Documented | Documentation Correct | Missing Documentation |
|---|---:|---:|---:|---|
| Indexed fuzzy search (full-text) | ⚠️ frozen path only | ⚠️ inline | ❌ | Rationale is wrong (D-4); canonical path does not use it (F-2). Needs T-9. |
| Candidate narrowing | ✅ | ⚠️ inline | ✅ | Needs T-9 §"Strategy"/"Why it is safe" |
| Graph / full-text indexes | ✅ created + verified | ⚠️ migration comment | ✅ | Needs the index table in T-9 |
| Query bounding (`max_graph_queries_per_turn`) | ✅ | ⚠️ inline | ✅ | Needs limits section |
| Prompt caching / invariant prefix | ❓ not located | ❌ | — | `NOT PROVABLE`. If absent, document the decision not to cache. |
| Config caching + invalidation | ✅ | ✅ inline (`runtime_activation.py:73-95`) | ⚠️ | Correct for API; **must state workers are excluded** (F-3) |
| Graph generation swap | ⚠️ built, unused | ❌ | ❌ | Needs T-7 |
| Incremental sync / watermarks | ✅ | ✅ inline (good) | ✅ | Needs a standalone doc |
| Batching (1,000-row SQL batches; `GRAPH_SYNC_BATCH_SIZE`) | ✅ | ⚠️ inline | ✅ | Needs a standalone doc |
| Retry / backoff | ✅ | ✅ inline | ✅ | Needs consolidation into one policy doc |
| Connection pooling | ❌ (`pymssql.connect` per operation) | ❌ | — | Document the gap and the intended pool |
| Model routing / cost optimization | ✅ tier escalation, failover, pricing | ⚠️ `ai/README.md` | ✅ | Needs cost-optimization rationale |
| Candidate-set caching (30 min TTL) | ✅ | ✅ inline (`search_strategy.py:80-87`) | ✅ | Needs T-9 §caching |

---

# Section W — Final Deduplicated Gap List

| ID | Severity | Capability | Gap | Layer | Dependency | Evidence |
|---|---|---|---|---|---|---|
| **F-1** | **P0** | Case workflow orchestration | `ReturnCaseWorkflow` is never started by production code; steps 12–22 of the business flow are unreachable | Backend | — | `graph_nodes.py:1002-1026`; `api/order_agent.py:223-228`; only starter is `tests/test_return_case_workflow_real_infra.py:172` |
| **F-2** | **P0** | Order Discovery search | Unfiltered, unordered `LIMIT 100` + client-side `difflib`; can miss the correct order | Backend | index already exists | `search_strategy.py:95,466-484,506-513`; `graph_nodes.py:678,752` |
| **F-3** | P1 | Runtime config / AI model hot change | Workers never refresh configuration or routes | Backend/Infra | — | `run_order_discovery_worker.py:27,59,84`; `run_return_workflow_worker.py:32`; `runtime_activation.py` used only by `main.py:569` |
| **F-4** | P1 | AI interception | `structured_invocation` (Order Agent + Graph Analyzer) bypasses pre-dispatch interception | Backend | — | `service.py:449` sole `interceptMode` reader; `structured_invocation.py` has none |
| **F-5** | P1 | Business-time waits | `business_calendar_id`/`timezone` declared, never read; timers are wall-clock | Backend | — | `return_case_workflow.py:128-129,492,497` |
| **F-6** | P1 | Bay Assignment | No warehouse/bay/return-location/confidence produced; `bay_result` signal has no production sender | Backend | F-1 | `return_case_activities.py:114-131`; `return_case_workflow.py:154-159,328-329` |
| **F-7** | P1 | RMA persistence | RMAs never written to the SQL return store on the canonical path | Backend | F-1 | `return_case_activities.py:196-243`; `sql_business_state.py:325` callers |
| **F-8** | P1 | Shipment / tracking management | No create/update API, no UI, no carrier/status/timestamp model | Backend + UI | — | no endpoint; `SupportConsolePage.tsx:379` single field |
| **F-9** | P1 | Approvals | Screen absent despite complete backend kernel | UI | — | `registry.ts:97-195`; `api/proposals.py` exists |
| **F-10** | P1 | Config-driven discovery fields | Tenth field requires Python edits in ≥4 places; discovery config bound to the frozen path | Backend/Config | — | `contracts.py:36-65`; `search_strategy.py:50,77-78,102-120,151-263` |
| **F-11** | P2 | Graph generation cutover | Blue/green built but every sync pins `legacy-live`; readers can see a partial rebuild | Backend | — | `sync_service.py:73-74,422,642,649`; `generation.py:19` |
| **F-12** | P2 | Schema change classification | No additive/compatible/destructive classifier; destructive changes refused, not migrated | Backend | F-11 | `analyzer_schema_addition.py:19-20,157-158` |
| **F-13** | P2 | Data Sources screen | Nested as a `/config` section, not an independent capability | UI | — | `registry.ts:72-83` `CONFIG_SECTIONS` |
| **F-14** | P2 | Agent failure policy | `failure_policy` never read at runtime; behaviour hardcoded; test asserts constants only | Backend/Config | — | `descriptor.py:36`; `tests/configuration/test_agent_failure_policy.py` |
| **F-15** | P2 | Duplicate implementations | Two Order Discovery stacks, two AI packages; frozen side is more capable in three respects | Backend | F-2, F-4, F-10 | `test_frozen_modules_gain_no_new_callers.py:33-59` |
| **F-16** | P2 | Per-screen contextual sidebar | 5 of 7 domains declare `sections: []` | UI | — | `registry.ts:109,125,149,174,193` |
| **F-17** | P2 | Case Operations completeness | No per-agent decision log, waits/deadlines view, or permitted interventions | UI + Backend | — | `registry.ts:192` self-declared `NO BACKEND YET` |
| **F-18** | P2 | Support Console completeness | No bay panel, no correction flow, no tracking management | UI | F-6, F-8 | `SupportConsolePage.tsx` |
| **F-19** | P2 | Adversarial test coverage | 10 of 37 mandatory scenarios absent; 4 false-confidence tests identified | Tests | F-2,3,4,8,10 | Section R |
| **D-1** | P1 | Documentation | `confirm_order` docstring asserts `ReturnCaseWorkflow` does not exist | Docs | F-1 | `graph_nodes.py:1023-1026` → **T-1** |
| **D-2** | P1 | Documentation | README ownership table wrongly marks SQL Server read-only | Docs | — | `README.md` → **T-2** |
| **D-3** | P1 | Documentation | Timing config docstring asserts business-calendar semantics that do not exist | Docs | F-5 | `return_configuration.py:296-298` → **T-3** |
| **D-4** | P1 | Documentation | Fuzzy-probe rationale rests on a false premise about APOC | Docs | F-2 | `search_strategy.py:91-95` → **T-4** |
| **D-5/6** | P2 | Documentation | README states four domains and a package tree that do not exist | Docs | — | → **T-5** |
| **D-7** | P2 | Documentation | `failure_policy` comment implies enforcement | Docs | F-14 | → **T-6** |
| **D-8** | P2 | Documentation | Legacy generation pinning unexplained | Docs | F-11 | → **T-7** |
| **D-9** | P2 | Documentation | No screen documentation exists for any of the ten screens | Docs | — | → **T-8**, Section U |
| **D-10** | P2 | Documentation | No optimization documentation exists | Docs | — | → **T-9**, Section V |
| **D-11** | P2 | Documentation | 126 API paths lack role/idempotency/concurrency/audit documentation | Docs | — | → **T-10** |
| **D-12** | P2 | Documentation | Configuration families lack classification/propagation/rollback documentation | Docs | — | → **T-11** |
| **D-13** | P3 | Documentation | 12+ superseded plan documents compete with current docs as truth | Docs | — | `docs/STAGE_4L_*`, `STAGE_4M_*`, `ORDER_DISCOVERY_*` |
| **F-20** | P3 | Security hygiene | `.env` / `.env.vault-backup` on disk; pre-`0615921` git history unverified | Infra | — | `NOT PROVABLE` — run `git log -p -- .env.vault-backup` |
| **F-21** | P4 | Cleanup | `ai_gateway/` re-export shim; ~20 modules still import it | Backend | — | `ai_gateway/__init__.py:1-16` |
| **F-22** | P4 | Cleanup | `interim_active_schema.py` (36 KB) frozen with zero importers | Backend | — | `test_frozen_modules_gain_no_new_callers.py:55-58` |
| **F-23** | P4 | Cleanup | Root-level `fix_eslint.py`, `fix_imports.py`, `backend.log`, `worker.log`, `validation_output_new.txt` committed | Repo | — | repository root |
| **D-14** | P4 | Documentation | README mojibake (`â€"`, `Â§`) | Docs | — | `README.md` |
| **D-15** | P4 | Documentation | `README-back.md` and undated `PACKAGE_MANIFEST.md` / `AGENTS.md` | Docs | — | repository root |

---

# Evidence Standard Statement

**Implementation evidence found** — every finding above cites file, symbol and line, read directly from commit `0615921`.

**Behavior proven against runtime/real infrastructure** — limited to:
- Frontend Vitest suite: 18 files / 168 tests passing.
- Frontend TypeScript build: `tsc -b` exit 0.
- Backend test collection: 2,793 tests, no collection errors.
- Backend targeted execution: `test_frozen_modules_gain_no_new_callers.py` + `dynamic_knowledge/test_search_strategy.py` — 43 passed.

**Marked `NOT PROVABLE`** — anything requiring live Mongo, Neo4j, Temporal or SQL Server. The full backend suite was executed during this audit and **aborted** at a 120-second per-test timeout in `tests/test_order_agent_rest.py::test_order_agent_scenarios`, which blocks awaiting live infrastructure; it therefore yields no suite-level pass/fail signal. Specifically not proven at runtime: end-to-end case flow (though F-1 makes the static conclusion unambiguous), sync against real sources, analyzer against real connectors, Vault resolution, and any assertion about behaviour under real concurrency.

A follow-up run against the full Docker Compose stack is recommended before acting on any finding marked `NOT PROVABLE`. It is **not** required for F-1, F-2, F-3, F-4, F-5, F-6, F-7, F-8, F-9 or F-10, each of which is established by static evidence that infrastructure cannot change.

**No inference was made** from: an endpoint existing to a UI working, a UI calling an endpoint to that endpoint working, a config key existing to hot reload working, a model key existing to runtime switching working, one gateway method supporting interception to interception being complete, or a mocked test passing to a requirement being met. Where those inferences would have been convenient — F-3, F-4, F-5, F-6, F-14 — the audit traced the consuming code and found the gap.

---

*End of report. Audited commit `061592121325af08765f000029faa559d4423210`, branch `refactor/unified-return-platform`.*
