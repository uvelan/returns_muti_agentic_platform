# Return Copilot — Execution State

**Authoritative record of where remediation stands.** Updated as each wave completes; this file,
not any agent transcript, is the source of truth.

**Plan:** [`RETURN_COPILOT_REMEDIATION_PLAN.md`](RETURN_COPILOT_REMEDIATION_PLAN.md)
**Started:** 2026-08-15 from `2878be0` + unpushed Copilot working tree
**Last updated:** 2026-08-15 — backend **3610 passed, 3 skipped, 475 deselected, 0 failed**; frontend typecheck clean

> **The phase table below is stale from wave 14 and is being reconciled.** Phases 5, 6 and 11 landed
> work after it was last written and are not "not started". Trust the DONE / DISCOVERED / BLOCKED
> sections and the wave log over the table until this note is removed.

---

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 · Baseline & freeze | **DONE** | env verified; backend suite green (Gate 0 met) |
| 1 · Runtime agent mapping | **DONE** | Gate 1 met at runtime; audit finding #1 closed |
| 2 · Case projection + freeze | **DONE** | route serves `CaseProjection`; Gate 2 met |
| 3A · Deterministic policy | **DONE** | gate landed; audit finding #4 closed |
| 3B · Durable Support events | **DONE** (backend) | client half + outbox read model in flight |
| 3C · Order confirmation | not started | needs Phase 2 |
| 4 · Cumulative Support / RMA | **DONE** | audit finding #5 closed |
| 5 · Shipment / tracking / label | not started | needs 2, 4 |
| 6 · Order lines + selection | not started | needs Phase 2 |
| 7 · Frontend binding | **in progress** | closes findings 2, 3, 8, 9, 12, 13 |
| 8 · Polling / reload / reconnect | **DONE** (client) | audit finding #11 closed |
| 9 · Warehouse + settlement | **DONE** (backend) | audit finding #16 closed |
| 10 · Recovery / reconciliation | **DONE** | audit finding #10 closed |
| 11 · Contracts + regression | not started | |
| 12 · Adversarial E2E | not started | |

---

## Environment

Verified at wave 1 start.

```text
git HEAD          2878be07433e76c9b75f1cc38985248568313f48
branch            refactor/unified-return-platform
working tree      unpushed Copilot changes intact (5 modified, 17 untracked)
compose stack     all 15 containers up; backend, temporal, mongo, neo4j,
                  sqlserver, valkey, vault all healthy
backend API       http://127.0.0.1:18000
backend venv      backend/.venv/Scripts/python.exe
test markers      unit · integration · live_infra (live_infra deselected by default)
```

### Backend test baseline — Gate 0 reference

```text
.venv/Scripts/python.exe -m pytest -m "not live_infra" -q
2874 passed, 3 skipped, 417 deselected, 8 warnings in 156.04s   exit 0
```

Taken against an unmodified tree (verified: no agent had written to disk at completion), so it
is a clean reference. Any later red is attributable to the change that produced it. The 417
deselected are `live_infra`; `scripts/dev/run_real_infra_suite.sh` selects them.

**Known environment gap — RESOLVED, see [Running the reasoning path](#running-the-reasoning-path-keyless-by-default)
below.** No STANDARD-tier reasoning *model* is configured on this host and none is needed: with
`MANUAL` in `PLATFORM_AI_PROVIDER_ORDER` a keyless turn is **held for a human** rather than
failing. Policy correctness never needed a provider at all — that is the point of the
deterministic evaluator.

---

## Running the reasoning path (keyless by default)

**Standing instruction: keyless by default. Real AI only when explicitly asked.**

### The failure this replaced

Every Copilot turn used to answer `503 ORDER_AGENT_LLM_FAILED`, and the worker log said:

```text
All ORDER_AGENT_REASONING_V1 routes and lightweight fallbacks failed
attempts=0  last_error=PROVIDER_UNAVAILABLE  failures=none
```

`attempts=0` is the whole diagnosis: nothing was tried because nothing was **constructible**. With
no `*_API_KEY` and no Vault reference, `_provider_credentials` returns an empty tuple for GOOGLE
and NVIDIA, so `build_routes` emits no route for either; SIMULATOR contributes only a LIGHTWEIGHT
*eligibility* route that this STANDARD task does not permit. An empty candidate set is not a
failure the failover loop can describe, so it reported the initial state of `_LoopState`.

Nothing in the task was wrong, and no code was missing. `ORDER_AGENT_REASONING_V1` has always
listed `MANUAL` in `allowedProviders`; `_provider_models` has always offered MANUAL at every tier;
`routes.py` has always exempted it from the credential requirement; and the whole
paste-JSON-in-the-console flow — `DurableInterceptionProvider`, the three
`/api/ai/interceptions/{id}/…` endpoints, the AI Control Center's `ManualResponder` — was already
built. The single missing piece was `ai_provider_order`, which named only providers that need a
key.

### Mode 1 — keyless, a human answers in the console (the default)

```bash
PLATFORM_AI_PROVIDER_ORDER=GOOGLE,NVIDIA,SIMULATOR,MANUAL   # MANUAL last: the fallback, not the default
PLATFORM_AI_MANUAL_HANDOFF=AUTO                             # UI when the process has a store
PLATFORM_AI_TIMEOUT_SECONDS=280                             # how long the operator actually has
PLATFORM_AI_GLOBAL_TIMEOUT_SECONDS=850
```

MANUAL is **last** on purpose. A deployment holding a credential builds GOOGLE/NVIDIA routes and
never reaches a human; a deployment holding none holds the turn instead of failing it.

The turn then blocks and the request appears in the AI Control Center's interceptions tab:

1. `GET /api/ai/interceptions` — the queue, identity and status only (prompts stay sealed).
2. `GET /api/ai/interceptions/{id}/request` — unseal the held request. It carries `systemPrompt`
   (~22 KB, including the `REQUIRED RESPONSE SCHEMA` block) and `userPayload`
   (`mode`, `contextJson`, the correction fields), which is everything needed to write a valid
   `AgentAction`.
3. `POST /api/ai/interceptions/{id}/answer` with `{"responseText": "<AgentAction JSON>"}` — or
   `/allow` to let the model proceed unchanged, or `/cancel` to abandon it.

One turn takes **one hold per `decide()`**: a discovery turn that searches and then answers holds
twice.

`ai_timeout_seconds`, not the provider's own 600 s, is what bounds how long an operator has —
`FinalDispatcher` wraps every `provider.generate` in `asyncio.wait_for`. The shipped 280 s sits
inside the provider's hold so a lapsed request expires visibly rather than being abandoned
silently.

**Nothing is relaxed for the human.** The answer is parsed against `AgentAction`, passes
`inspect_output`, and meets `ResponseSafetyGuard`/`HallucinationGuard` exactly as a model's would.
A free-text paste fails as `RESPONSE_INVALID` and the turn 503s — verified at runtime, see the
DONE entry. And it is reported as `MANUAL` / `manual-human-v1`, never as the provider whose place
it took, so an evaluation set built from traces can never absorb human text as model output.

**Production:** unreachable, and no new gate was added because two already exist.
`Settings.validate_relationships` refuses `MANUAL` in `ai_provider_order` outside
development/test, so the route cannot be built; and both manual providers raise
`ProviderError("POLICY_BLOCKED")` outside those environments.

### Mode 2 — record one real run (only when explicitly asked)

`Settings.ai_replay_mode` is `OFF | REPLAY | STRICT`, and `ReplayProvider` wraps **every** route,
so no single unwrapped route can quietly cost money.

```bash
PLATFORM_AI_PROVIDER_ORDER=GOOGLE          # or NVIDIA/OPENAI/ANTHROPIC
PLATFORM_GOOGLE_API_KEYS='["<key>"]'       # dev/test only; elsewhere use *_API_KEY_REFERENCES
PLATFORM_GOOGLE_STANDARD_MODELS='["models/gemini-3.6-flash"]'   # STANDARD tier — the reasoning task's tier
PLATFORM_AI_REPLAY_MODE=REPLAY
```

`ORDER_AGENT_REASONING_V1` is `tier: STANDARD`, so a key alone is not enough — the provider's
**standard** model pool must be non-empty or the route is still never built.

In `REPLAY` a miss is a real call that is then recorded, so **the corpus builds itself**: run once
and every request is captured. Recordings are keyed by a digest over the system prompt, the user
payload, the response schema and the decoding parameters — not by conversation or turn position —
so changing the prompt by a character correctly misses instead of replaying an answer to a
different question. A recording keeps the real provider and model, so a replayed trace stays
attributable. Recordings live in the platform system store, and deliberately **not** alongside the
interception records: those hold text a person typed.

### Mode 3 — CI replays the recorded reasoning, proving no provider was reached

```bash
PLATFORM_AI_REPLAY_MODE=STRICT
# no key needed: ReplayProvider.configured is True in strict mode
```

A miss is `ProviderError("REPLAY_MISS")`, so a green run is *proof* nothing left the process —
which is the only way a token-free evaluation run is actually token-free rather than mostly so.
This is the mode that lets CI exercise real model reasoning without a credential; Mode 1 is the
one that lets a developer drive the path with no recording at all.

---

## OPEN — the Configuration page needs its own audit

**Operator finding, 2026-08-16: the config page "is not as expected".** Investigated far enough to
name the cause; the page itself has not been audited and should be.

**You can promote a configuration release, but you cannot create or edit one over HTTP.** The
running app serves exactly:

```text
GET  /api/config/releases
GET  /api/config/releases/{id}
POST /api/config/releases/{id}/promote
```

There is **no** `POST /api/config/releases` (create draft) and **no**
`PATCH /api/config/releases/{id}/domains/{key}` (edit a domain). Both handlers exist and are
validated — `configuration/api/releases.py` defines them on a router with prefix
`/data-console/v1/configuration` — but `main.py:1303` records that router as deliberately
unmounted:

> *"Wave F1: the Data Console routers are no longer mounted. Eighteen routers served
> `/data-console/v1/*`… Their only consumer was the legacy frontend, which Wave F4 deleted.
> **Unregistered, not deleted.** `configuration.py`, `sources.py`, `audit.py` and `auth.py` still
> export handler functions that the canonical `/api/config` router imports directly."*

**`releases.py` is not among the four re-exported.** So the edit half of the release lifecycle is
reachable only by a direct Python call.

**Why the tests do not catch it.** `test_partial_agent_behavior_edit_activates_without_restart` and
`test_ai_prompts_and_simulation_behavior_activate_from_graph` both pass — they invoke the handler
*function*, never an HTTP route. A capability can therefore be fully tested and completely
unreachable at the same time, which is what happened here.

**Consequences observed:**

* **A prompt change cannot be made from the UI or the API.** Prompt v14 (staged customer→product→
  order confirmation, warmer tone) is in `config/ai_gateway.yaml` and requires a **backend image
  rebuild** to take effect — the packaged YAML is baked into the image, and the release API that
  would have avoided the rebuild is the unmounted half.
* **The Configuration and AI Control Center pages render summaries with no interactive controls**
  at all — no buttons, tabs or inputs in the DOM. Consistent with there being no write API to build
  an editor against, but the pages have not been audited and may have their own defects.

**Recommended fix:** re-mount the release write routes under `/api/config` (the same
direct-import pattern the other four modules already use), and add a contract test that asserts the
*route* exists rather than only the handler. Until then, "change the prompt from the UI" is
aspirational rather than true.

---

## Phase 12 — adversarial E2E, RUN 2026-08-15

**First end-to-end proof in this programme.** Three cases against the two real Ferguson orders,
keyless (every `decide()` held by `MANUAL` and answered through the operator route), all on ACTIVE
generation `2ee99fc8`. One reached `COMPLETED_EXTERNAL_SETTLEMENT`, revision 61, `awaiting=[]`,
terminal.

**The anti-fabrication results are the ones that matter**, since inventing data is what the audit
was about. A claimed `total_found: 7` against evidence carrying 1 was **refused**, naming the
mismatch; so was a fabricated tracking number. A non-existent order returned `NO_MATCH` and the
copilot said it could not answer. A comment line with unknown quantity projected
`returnableQuantity: 0` with `ORDERED_QUANTITY_UNKNOWN` rather than guessing. Replay was
`DUPLICATE` three ways with **0 extra model calls** and a byte-identical response. Stale revision
→ `409`, no state change, revision strictly monotonic across 13 writes. Concurrent Support
updates lost nothing. `STOCK_CLASS_FROM_CONFIGURATION` appeared on the approval taken from the
configured default, exactly as designed.

Eight defects found — **D1 and D4 fixed**, see below; D2/D3 in flight; D5/D6 fixed; D7/D8 open.

**Structural finding not in the numbered list:** `clarification_policy.fields` carries twelve
fields and **every one is a discovery anchor** (order number, customer id, tracking, email, phone,
ZIP, SKU…). None is a policy fact. Return reason and condition live in `selection_vocabulary`,
captured at line selection — but **nothing writes `condition_new` / `suitable_for_resale` as case
facts and nothing maps a selection condition onto them.** So the deterministic evaluator cannot be
driven from the associate path at all: a deterministic REJECT is unreachable and the only route to
APPROVE is a supervisor override. Phase 3A's evaluator and its 173 tests are sound; the bridge is
simply absent. **The operator's decision to narrow policy to the return-window check removes the
need for that bridge entirely** — the window needs only a purchase date, which the confirmed order
carries.

### D1 + D4 — CLOSED 2026-08-15

Carrier tracking reached `dbo.return_tracking`, resolved the right `caseId`, and **never appeared
on the projection** — `project_shipments` read `trackingReference` off `dbo.return_record` and
nothing joined the tracking table in, so `awaiting` stayed `['LABEL','TRACKING']` and **a copilot
polling for tracking polled forever.** D4 was the same read model being single-slot, making two
parcels on one RMA unrepresentable. Both fixed together; suite **3709 passed, 0 failed**. The
tests name the behaviour:

```text
test_an_applied_carrier_update_puts_the_parcel_on_the_projected_record
test_the_polling_copilot_stops_waiting_for_tracking
test_a_split_return_projects_two_parcels_each_with_its_own_identity      (D4)
test_the_case_revision_moves_with_the_parcel                             (6.5 held)
test_replaying_the_same_carrier_event_records_no_second_parcel
test_no_parcel_is_recorded_for_an_rma_the_case_store_does_not_own
test_the_carriers_own_status_vocabulary_is_never_mapped_onto_the_contract
```

### Still open from Phase 12

| | |
|---|---|
| **D2** | delivery-claim `reporting_window` never reaches `slaDueAt`; every writer is a fixed offset. In flight |
| **D3** | **HIGH** — warranty / delivery-claim cases can never complete; `_unresolved_dimensions` returns the dimension unconditionally with no clearing condition. Contradicts the operator's explicit "not terminal, rejoins the RMA lifecycle". In flight |
| **D7** | `confirmedOrder.candidateSetId` / `candidateId` project as `null` though `CONFIRM_ORDER` carried both — the exact audit trail that makes "a model confirmed an order it invented" detectable |
| **D8** | `POST /api/return-shipments/{ref}/updates` accepted `TRK-NOT-REAL` against a never-issued RMA, stored with `caseId: null`. **Declared** behaviour, not an oversight — but no existence or authorization check, so any string is storable against any reference. Given `TRK-98421049281`, worth a second look |

---

## Integration pass — 2026-08-15

Rebuild + republish, run together as D20 requires. **D16, D17 and D20 all closed.**

```text
Vault              was SEALED -- root cause of six workers crash-looping on the literal
                   host `vault-resolved.invalid`, and of `backend` unhealthy for 2h.
                   Unsealed from .vault-local/init.json; all seven recovered.
OpenAPI            regenerated, ~27k lines. D30 did NOT reproduce -- export_openapi.py
                   exits 0 standalone now. Backend 163 contract tests + frontend 462 green
                   against the regenerated document.
images             rebuilt. NOTE: `docker compose up -d` silently did NOT recreate --
                   containers stayed on 2487164f while the new image was e5b1a17b.
                   `--force-recreate` was required.
config             republished without `--if-missing`; ConfigurationHead ACTIVE ->
                   return-platform-058d349ed4d0fb4c, previous SUPERSEDED.
D16 CLOSED         GET /api/cases/{id} -> HTTP 200 with a real CaseProjection carrying
                   confirmedOrder.orderReference "CQ363350". Was 500 AttributeError.
D17 CLOSED         deployed image: TemporalSignalDispatcher importable and registered
                   unconditionally for SUPPORT_RESPONSE_SIGNAL_TOPIC. Was ADAPTER_NOT_
                   CONFIGURED because the image predated 3B. Outbox index plans: 6 passed
                   on real Mongo.
D20 CLOSED         last `runtime_configuration_refresh_failed_using_last_good_snapshot`
                   at 14:56:59; head moved 14:57:03; silent since. The workers recovered
                   four seconds after the republish landed.
```

**D11's fix does not cover nested key additions.** The republish reported
`active_release_no_longer_validates ... return_policy.return_method_requirements Field required`
and fell back to the packaged configuration, warning that operator values were not preserved.
D11's merge is **top-level** shallow, so the old release's `return_policy` block shadowed the
packaged one wholesale and the newly-required nested key was lost with it. It degraded safely
and loudly — but the merge is not sufficient for nested additions. Raise alongside D11.

**D51 — the documented "run without Vault" override does not work.**
`compose.novault.yaml` exists precisely to escape the sealed-Vault problem, and its own header
says so. It supplies all seven credentials from `.env` (verified present) and clears the
`*_SECRET_REFERENCE` variables. But `runtime-configuration-init` fails immediately:

```text
bootstrap_graph_configuration.py:137
    RuntimeError: Runtime bootstrap requires the Vault secret resolver
```

`main()` calls `resolve_runtime_settings_from_vault(...)` and raises unconditionally when the
resolver is `None`. Every app service `depends_on` that init completing successfully, so the
whole app profile refuses to start. **The override covers the services but not the bootstrap
that gates them.** Options: let the bootstrap accept explicit settings when
`PLATFORM_VAULT_ENABLED` is false, or exclude the init from the no-Vault profile given the
release is already published. Not fixed — it needs stack restarts, which would collide with the
Phase 12 E2E now running. Stack reverted to the Vault path (unsealed, healthy) meanwhile.

**The full `live_infra` sweep still has no result, and that is an orchestrator invocation
failure, not a code one.** Two attempts wasted: the first piped through `tail -60`, discarding
everything before the last 60 lines; and `--timeout=300` on Windows kills the whole pytest
session rather than the offending test, which it did, inside a fixture. **Re-run unattended,
capturing full output to a file with `-rA`, and without a per-test timeout** — rely on the
outer `timeout -k 5` alone. The 7 errors + 5 failures remain unexamined; 5 were previously
attributed to the `sqlserver` container restarting mid-run, not to code.

---

## DONE

### Keyless reasoning · a turn with no credential is held, not failed — runtime-proven

`attempts=0 last_error=PROVIDER_UNAVAILABLE` closed. `MANUAL` appended (last) to
`PLATFORM_AI_PROVIDER_ORDER` in `.env` and documented in `.env.example`. **Configuration only** —
no gate was added or relaxed, because production is already refused twice over (settings
validation on the provider order, and `POLICY_BLOCKED` in both manual providers).

One code fix was needed to verify it in isolation: `scripts/run_order_discovery_worker.py` called
`create_order_discovery_worker(temporal, activities)` without a task queue, so the worker always
polled the module default while `main.py` *dispatched* to
`settings.order_discovery_workflow_task_queue`. The setting was half-wired — pointing a deployment
at its own queue moved the dispatch and left every worker listening elsewhere, so turns went
nowhere.

Proven against the live stack, from the working tree, on a private task queue
(`return-platform-order-discovery-manualproof`) with a worker and API on port 18010 so the
deployed stack was never diverted. All four `*_API_KEYS` empty:

```text
turn 1  POST /api/v2/order-agent/conversations/manualproof-1786784935/turns  → blocks
        GET  /api/ai/interceptions  → 6fcbf6f6… PENDING, created 09:09:05
        held payload: systemPrompt 22218 chars incl. REQUIRED RESPONSE SCHEMA;
                      userPayload.mode=DECIDE; contextJson carries user_message,
                      the six agent_policies capabilities, the 8+ identification fields
        answered with an ORDER_SEARCH AgentAction  → the real graph search executed
        second hold d32a3f85… → answered with a RESPOND
        → HTTP 200, model_provider=MANUAL, model_name=manual-human-v1,
          query_evidence carries real plan/compiled/result checksums against
          schema_version 2026.08.04, graph_generation_id legacy-live
```

The search returned `total_found: 0` honestly — this stack's Neo4j holds **zero `SalesOrder`
nodes**, so no order reference can match. That is a seeding gap (see DISCOVERED), not a reasoning
one; the pipeline carried the search and its evidence correctly.

Guards verified intact in the same session: a free-text paste that even named an order and asked
to confirm it was refused, never acted on —

```text
order_agent_response_invalid provider=MANUAL model=manual-human-v1 error_type=JSONDecodeError
order_agent_model_attempt_failed attempt=1 tier=STANDARD  provider=MANUAL error=RESPONSE_INVALID
order_agent_model_attempt_failed attempt=2 tier=LIGHTWEIGHT provider=MANUAL error=RESPONSE_INVALID
order_agent_model_attempts_exhausted attempts=2 last_error=RESPONSE_INVALID
```

`attempts=2` rather than `attempts=0` is the signature of the fix: the route now exists and is
tried, and a bad answer fails validation instead of bypassing it.

Regression cover: `backend/tests/test_keyless_reasoning_is_held_for_a_human.py` (10 tests) pins
that a keyless `build_routes` yields a STANDARD MANUAL route, that `AIRoutePool.candidates`
actually returns it for the packaged task, that **removing MANUAL reproduces the empty candidate
set** (so the diagnosis survives the fix), that a real credential outranks the human, and that
MANUAL is refused in production.

### Phase 2 route · `GET /api/cases/{caseId}` serves `CaseProjection` — Gate 2 met

Migration option (a): one shape per resource. `CaseDetail`, `CaseReturnRecord`, `CaseReturnItem`
deleted from `api/cases.py`. `list_cases` keeps `CaseSummary` but its `status` is now the
**projected** `ReturnCaseStatus` — a list saying `CLOSED` beside a detail saying
`COMPLETED_EXTERNAL_SETTLEMENT` is the drift `status_mapping` exists to end.

**D21 closed properly:** `_requirement_table` builds from the active release with **no fallback** —
a missing release is `503 RETURN_CONFIGURATION_UNAVAILABLE`, because silently falling back to the
code default is precisely D21. Proof is well-built: a release is constructed where
`PREPAID_PARCEL` requires `[RMA]` only against the code default's `[RMA, LABEL, TRACKING]`, a
guard test first asserts the two tables **disagree** so the assertion can discriminate, then the
same case is fetched under each.

**Runtime proof on the real stuck record** (`4e372a39…`, tenant-ops01, working tree on a spare
port):

```json
"stage": "AUTHORIZED_RMA", "revision": 5, "isTerminal": false,
"awaiting": ["POLICY","RETURN_METHOD"],
"artifacts": [{"artifactId":"LBL-OPS01","artifactType":"SHIPPING_LABEL",
               "shipmentId": null, "active": true}],
"shipments": null, "warehouse": {"bayReason":"PRE_ARRIVAL_NOT_ALLOWED", ...},
"settlement": {"status":"NOT_INTEGRATED"}
```

The label is served and attributed to no package; **no shipment was invented**; polling continues.
`awaiting` is `[POLICY, RETURN_METHOD]` rather than `TRACKING` — correct, and exactly §6.4: the
completion profile is unresolved, so `awaiting` is the unresolved dimensions. Tenant isolation
verified live: the same id from `default` returns 404, byte-identical to a nonexistent id.

**Two premises in the orchestrator's brief were wrong and were corrected by the agent:**
`warehouse` does *not* give every `bayRecommendation` value a typed home — only three of seven
have fields — so `bayRecommendation` was **kept**, retargeted onto `CaseProjection.facts`;
deleting it would have deleted four published values. And `CaseProjection` carries **less** than
`CaseDetail` for the two consoles (no `workflowId`, `sessionId`, `configurationReleaseId`,
`graphGenerationId`), so each loss is stated on its panel through the existing `NotPublished`
idiom rather than silently dropped.

### Phase 10 · Recovery — **audit finding #10 CLOSED**

Pure classifier (`case_divergence.py`, no IO, no Temporal) + a recovery service, two routes, and
consumption of the outbox's dead letters **in the process that produces them**. 39 tests.

**All six real orphans classified read-only** — case versions and `updatedAt` byte-identical
before and after. All six: `RECOVERY_REQUIRED · recoverable · late event DRIVES_RECOVERY`.

**Sharp detail:** the classifier treats `CLOSED` and `ABSENT` as one decision with two reason
codes. The six executions were `TERMINATED` when the audit was written and have since **aged out
of retention**, so today they read `ABSENT` — a classifier keyed on `TERMINATED` alone would
silently have stopped detecting them.

**A seventh orphan, of a different kind:** `d3190045…` is `RMA_RECEIVED` (non-terminal) with a
**`COMPLETED`** execution — the workflow finished while the case still expected work. That is the
case holding record `4e372a39…` with its null tracking.

No duplicate launch is possible by three layers: probe before start (a `RUNNING` execution never
reaches the launcher), Temporal id uniqueness settling the probe→start race, and terminality
decided on the **persisted** status first — so no Temporal outage can make a finished case look
recoverable. `CaseWorkflowResume` deliberately does **not** claim a delivered-events set:
"delivered" is not "applied", so recovery requeues only commands that never delivered.

### Phase 9 · Warehouse + settlement — **audit finding #16 CLOSED**

**Three of eleven warehouse fields have producers**; the other eight are `None`, each documented
with why. The refusals are the valuable part:

- `condition` exists on `case_return_items` — but it is the condition the **associate stated at
  selection**, not an arrival inspection. Reading it would relabel a customer's claim as a
  warehouse finding.
- `warehouseStatus` **has** a producer, on the wrong aggregate: `WarehousePlacementService.assign`
  writes to `ReturnSessionView` and keys handling units by session. Copilot cases have no session.
- `disposition` — no column, no writer. (`api/return_support.py`'s `disposition` is an idempotency
  outcome for a support event, not goods.)

`bayReason` became its own field rather than folding into `warehouseStatus`, because
`has_receipt` reads that — so folding would make every case that merely *asked* for a bay report
goods booked in and jump the Copilot to `WAREHOUSE_RECEIVING`. A receiving pane lit by a
recommendation is exactly the fabrication this block exists to prevent.

Settlement now **positively states** `NOT_INTEGRATED` on every case rather than being absent.
Absence reads as "not computed", which invites waiting for a credit memo that is never coming.
`CLOSED → COMPLETED_EXTERNAL_SETTLEMENT` is now taken on that assertion.

### Phase 8 · Frontend contract, polling, revision, reload — **audit finding #11 CLOSED**

29 files / 340 tests (was 27 / 300). Lint and typecheck silent.

**The audit's original defect is now asserted dead:** a case with an RMA and null tracking and
null label **keeps polling**. The stop condition is `isTerminal`, not `businessComplete` (a
rejected case is never business-complete and must still stop the client) and certainly not
`returnRecords.length > 0`.

**The revision guard is wired as React Query `structuralSharing`**, so a rejected response never
becomes query data at all — not "overwritten later", never applied. It discards strictly-lower
revisions only. **Equal revisions are explicitly accepted**, and the reason is the D22 mitigation:
until every child writer bumps the case, the assembler's read-case-first ordering means a
projection's revision can be older than the children it carries, so a same-revision body is a
legitimate carrier of new children — the label that arrived without a bump. Discarding equals
would freeze the screen from the other end.

**Beyond the brief, and correct:** putting `caseId` in the URL makes it shareable, so it can
arrive stale or cross-tenant. Polling now stops on a 4xx and refuses to retry one — otherwise a
403 polls forever at 10s. 5xx and network errors still poll, bounded.

**Judgement call accepted:** `latestFacts` was **kept**, not deleted. The contract does serve the
projection, but the route still returns `CaseDetail` and two live consoles read it — so removing
it today removes the only copy, not a duplicate. Left with a deprecation block naming the exact
delete condition.

### §6.5 revision invariant — 4 of 6 writers, proven on real Mongo

`append_case_fact`, `create_return_record`, `update_return_record`, `create_case_return_item` now
bump `cases.version` + `updatedAt` **atomically** with the child write — one
`session.with_transaction` per writer, the idiom already used in `repository.py:2008`.

Three design calls worth keeping:

- **`with_transaction`, not a bare `start_transaction`.** Two child writes bumping the same case
  collide at the server; MongoDB labels the abort `TransientTransactionError` and the driver
  re-runs the callback. A bare transaction would surface that to callers as a write conflict on a
  case nobody edited. Every callback is written to be re-runnable.
- **A blind `$inc`, deliberately no `expected_version`.** A compare-and-set there would turn every
  concurrent Support reply into a spurious conflict on a field neither writer touched — and would
  itself be a read-modify-write, i.e. the very lost update the test exists to catch.
- **`bump_case_revision(session=...)` takes the session as a required keyword with no default**, so
  a caller without a transaction cannot invoke it and believe the invariant holds.

**The concurrency assertion is not unit-level.** Against real Mongo in an isolated database: 8
writers released from an `asyncio.Barrier` across three child collections, asserting
`after == before + 8` and every child landing. `$inc` being atomic, 8 successful writes can only be
8 distinct strictly-increasing revisions unless one was lost — a read-modify-write repair fails it
two ways at once. 10 real-infra tests, 257s.

### Phase 3A.7 · The policy gate — **audit finding #4 CLOSED**

The gate is an **insertion**: `run` gained three statements and no existing method was
restructured — deliberately, so Phase 4's structural rewrite of the same loop rebases cleanly.
Orchestrator-verified in the source: `_policy_cleared` sits at line 681, `_open_support` at 690,
with the intervening comment stating the adjacency *is* the guarantee. Ninth activity registered
(was eight — that count was the audit's proof policy was absent).

66 new tests. Tri-state assembly enforced against the three provenance fields the fact log
actually carries: a superseded fact, an `INFERRED` acquisition, or an unrecognised method all
resolve to `UNKNOWN` rather than to `FALSE`. Excluded facts are **persisted** as
`policy_facts_excluded`, so "went to review because the only `installed` fact was a model's
inference" stays distinguishable from "nobody asked". Contradictory *stated* facts raise rather
than the code choosing which to believe.

`RETURNS_POLICY_OVERRIDE` is declared in `ALL_CAPABILITIES` only, so holders are exactly
`ADMIN_ROLES` and the service account is excluded **by construction** rather than by a check.

### Integration pass 1 · OpenAPI regenerated — **suite fully green**

All four snapshots + the generated TypeScript regenerated once, after the last schema-touching
agent landed. `tests/test_openapi_contract_drift.py` → **6 passed**. Backend suite: **3331 passed,
3 skipped, 431 deselected, 0 failed.** Deferring this through eight waves was correct — each
schema change would otherwise have invalidated the previous regeneration.

### Outbox index ownership (D18) + misconfigured-agent 503

One `ensure_integration_outbox_indexes(database)` in the module that owns the collection's schema,
following the `ensure_support_event_indexes` precedent. Union of **five** indexes; the API now
builds them once per boot instead of twice.

**The trap was real and is proven avoided** — server `explain`, not inspection:

```text
operator listing  find({}).sort({createdAt:-1})
  → LIMIT → FETCH → IXSCAN createdAt_-1        no COLLSCAN, no SORT stage
Phase 10 sweep    {status: DEAD_LETTER, reconciliationState: REQUIRES_RECONCILIATION}
  → FETCH → IXSCAN status_1_reconciliationState_1   both terms covered (was a full scan)
```

`createdAt_-1` was built only by `ReturnSupportService` — the copy the "obvious cleanup" would
have deleted, silently turning the operator listing into a collection scan plus in-memory sort.

**Sharp catch:** indexes are deliberately left at MongoDB's default names. Every deployed
environment already carries these key patterns unnamed, so naming them now makes `create_index`
raise `IndexOptionsConflict` (85) against identical keys — turning a consolidation into a boot
failure.

A misconfigured agent binding on the turn route is now `503 COPILOT_AGENT_CONFIGURATION_INVALID`
rather than `422 ORDER_AGENT_OUT_OF_SCOPE`; a genuinely out-of-scope agent stays 422. Conflating
the two is what made the original P0 read as a client bug for as long as it did.

### Phase 2 repository assembler + backfill

`load_case_projection_state(case_id) -> CaseProjectionState | None` in `case_repository.py`, plus
a total `CaseStatus → ReturnCaseStatus` map and a pure backfill planner. 51 new tests; 178 across
the projection package. Deliberately **not** tenant-scoped — it mirrors `get_case`, and the route
keeps its own `_belongs_to` check. It also deliberately does not call `project_case`: a repository
returning the derived shape would be a second place stage logic could grow.

Backfill emits only **two** actions, because stage is derived and legacy records are projected on
every read — writing either into Mongo would create a second copy of a derived value. Idempotence
is a property of the pure planner (second run returns `is_noop`), which is stronger than "wrote
equal values".

### Configuration health · plan §5.4 satisfied

One decision point — `evaluate_configuration_health(configuration, known_agent_policy_ids)` —
running both validators and **collecting** failures rather than raising on the first, so an
operator fixing a dangling agent mapping does not redeploy to discover the eligibility policy is
also missing. `require_healthy_configuration(..., environment=)` raises in production (following
the Vault rule, with `PRODUCTION_ENVIRONMENT` now shared rather than two literals) and reports in
dev. `probe_configuration` joins the existing `/health/ready` probe set as a seventh dependency —
not a parallel mechanism — single-flighted on the same 2s TTL.

Observed live: seven dependencies all `HEALTHY`, `configuration.healthy: true`, `failed_checks: []`.

The return-method requirement table moved to `return_policy.return_method_requirements` in
`production.yaml`, nine rows, with the four **inferred** rows under an explicit
`OPERATOR REVIEW REQUIRED` banner and a test asserting exactly those four sit below it. Guards
kept one home via a `@model_validator` constructing the real table.

### Phase 3B client half · Support console contract — runtime-proven

`supportEventId` is **required** in the TypeScript type (a default inside the request function
would mint a fresh id per retry — exactly what the backend refuses), minted at the user-action
boundary in `useState` and passed as a React Query mutation *variable*, so a retry re-runs with
the id it was given rather than reading current state. `reconciliationState` surfaced on
`OutboxView`. Frontend 27 files / 300 tests.

Proven against a **second, isolated container** built from the working tree on port 18001 —
the shared stack was left untouched with two agents live, then the container removed:

```text
A  first send, event id 1                → 200  RECORDED   outbox 2354e0a8…
B  resend, same id, same payload         → 200  DUPLICATE  outbox 2354e0a8…  (same command)
C  new answer, different id              → 200  RECORDED   outbox 9f931394…  (new command)
D  no supportEventId  (the old console)  → 422  SUPPORT_EVENT_ID_REQUIRED
E  same id, different payload            → 409  IDEMPOTENCY_CONFLICT
F  id via Idempotency-Key header         → 409  IDEMPOTENCY_CONFLICT  (header equivalence works)
```

Two event ids produced exactly two outbox commands; the replay produced none.

### Phase 2 §6.2–§6.6 · Projection contract, enums, completion, stage

New package `operations/case_projection/` (6 modules, 1703 lines) + 127 tests. ruff/format/mypy
clean. No existing file touched.

**Mutation-tested rather than trusted:** 13 targeted mutations, 0 survivors — removing the
completion guard, swapping `effectiveDecision`→`originalDecision`, reversing the precedence,
moving `RECOVERY_REQUIRED` into the terminal set, `all`→`any` on label satisfaction. One mutation
initially *survived* because the completion guard existed in two places, so neither was
individually testable; restructured to a single mechanism. That is the failure mode a green
first run hides.

Three structural guards make an empty requirement set unreachable: every row must include `RMA`;
no row may name `UNKNOWN`; an unmapped method leaves the profile unresolved with
`awaiting=[RETURN_METHOD]` rather than "requires nothing".

### Phase 1 follow-up · three defects closed

Mock mode restored (`agents` block added; verified end-to-end — a real turn renders and the
right pane advances). `ConversationPane` gained a `disabled` prop so the composer can be disabled
**without** the "Searching order graph…" spinner claiming a search that is not running — the
fabricated-state defect class this programme exists to remove. No layout, styling or grid change.
`return_history.py`'s `_AGENT_ID` literal — a second hardcoded copy of the routing key, one
module behind Phase 1's — now resolves from configuration through the same validator.
Frontend 27 files / 297 tests green.

### Phase 3B · Durable Support delivery — **audit finding #6 CLOSED**

`submit_return_outcome` no longer signals Temporal synchronously. It commits a support event plus
an outbox command in one transaction and returns; delivery rides the **existing** lease-based
outbox via one new `TemporalSignalDispatcher` on topic `return-case.support-response.signal`.
Unique constraint on `(caseId, supportEventId)`. 40 tests, all `integration`. `mypy src` clean
across 555 files. `workflows/return_case_workflow.py` untouched, as required.

The 500-on-closed-workflow defect is gone: a permanently undeliverable command dead-letters with
`reconciliationState: REQUIRES_RECONCILIATION` instead of losing the RMA. Classification is **by
exclusion** — an unrecognised future Temporal status retries rather than dead-letters, which is
the safe direction.

Guarantee stated correctly throughout: **at-least-once transport, effectively-once processing**
keyed on `supportEventId`. Support can now file an outcome while Temporal is down.

### Phase 3A config wiring — orchestrator

`ReturnEligibilityPolicy` wired into `ReturnPlatformConfiguration`; the Ferguson rule set is in
`config/returns/production.yaml`. Verified: packaged config parses, policy resolves as
`ferguson-standard-return-policy 2026-08-15`, and `restocking_fee.percentage` stays `None` — a
release trying to write a percentage fails to parse. 477 passed across configuration, api,
bootstrap, policy and graph-configuration suites.

**Design decision.** The field is optional on the model with a separate
`validate_return_eligibility_policy()` refusing activation when absent — mirroring Phase 1's
`validate_copilot_agent_binding`. Required-on-the-model would have broken every stored payload
predating the key (see D11). The distinction that matters: **absent policy is an operational
failure, never `REVIEW_REQUIRED`.** Degrading to review would look like the evaluator working
while no rule set was published at all.

### Phase 1 · Runtime agent mapping — **audit finding #1 CLOSED**, runtime-verified

`CopilotConfiguration.order_discovery_agent_id` in return configuration; `RuntimeConfigAgents`
on `/api/runtime-config`; frontend reads it via `useRuntimeConfig()` and fails closed when
absent. The `"order_discovery"` literal is gone.

Orchestrator-verified against the live stack, not taken on report:

```text
GET /api/runtime-config   → agents.orderDiscovery = "order-discovery-agent"
                             release return-platform-ca14f97fef6d7692
turn with "order_discovery"        → 422 ORDER_AGENT_OUT_OF_SCOPE   (unchanged)
turn with "order-discovery-agent"  → 503 ORDER_AGENT_LLM_FAILED     (cleared the gate)
```

The 503 is D2 — no reasoning provider on this host — not a regression. The policy gate is
passed, which is what Gate 1 asks.

A test asserts the *configured* id is sent by making the configured value `some-other-agent`, so
swapping one literal for another cannot pass.

### 3A.2–3A.5 · Deterministic policy evaluator — orchestrator-verified

New package `src/return_platform/policy/` (7 modules) + `tests/policy/` (113 tests).
`ruff`, `ruff format`, `mypy` all clean. **No existing file edited.**

Independently re-verified by the orchestrator, not taken on report:
`PolicyOutcome(route=WARRANTY, decision=APPROVE)` raises `ValidationError`; `MonetaryAmount`
rejects `float`. A bare input with every fact `UNKNOWN` yields `REVIEW_REQUIRED`.

Structural guarantees worth keeping: an `APPROVE` carrying any approval-forbidding reason code
raises at construction, so a future reordering of the branch logic fails a constructor rather
than shipping an approval; `RestockingFeeConfiguration.percentage`/`.amount` are typed `None`, so
a release writing `percentage: 15` fails to parse.

### Phase 2 commit 1 · `case_repository.py` extraction — orchestrator-verified

`repository.py` 2695 → 2358 lines; `operations/case_repository.py` = 386 lines, 17 methods,
mixin inherited by `OperationalRepository`. **Zero call sites changed** (17 files call these
methods; none edited). `tests/operations` + `tests/api` = 267 passed.

Verified independently: `ConcurrencyConflictError` is the *identical class object* from both
`operations.errors` and `operations.repository`; MRO is
`OperationalRepository → CaseRepository → object`; all 17 methods resolve to the mixin.

Two judgment calls accepted: (1) four extra methods moved (`find_case_by_confirmation`,
`bind_case_workflow`, `update_case`, `latest_case_facts`) — contiguous in the same band, and
splitting `latest_case_facts` from the `list_case_facts` it calls would have been worse;
(2) `ConcurrencyConflictError` relocated to a new `operations/errors.py` to break a genuine
import cycle, re-exported for the 11 modules that import it from the old path.

Equivalence proof, absent live infra: all 17 methods compared against `HEAD` on `co_code`,
`co_consts`, `co_names`, `co_varnames`, `co_flags`, signature, defaults, docstring and
annotations — **0 divergent** — plus identity checks on every global they resolve.

### Documentation landed:

- `RETURN_COPILOT_AUDIT_2026-08-15.md` — 16 findings, evidence base
- `RETURN_COPILOT_REMEDIATION_PLAN.md` — the plan, amendment history, traceability matrix
- `RETURN_COPILOT_PARALLEL_EXECUTION.md` — contention map, crew shapes
- `RETURN_COPILOT_POLICY_BASELINE.md` — the eligibility rule set (moved into the repo so
  Phase 3A has an in-tree authority)

---

## DISCOVERED

| # | Finding | Impact |
|---|---|---|
| D1 | **CONFIRMED (3A.1).** No source field carries stocked vs special-order | Policy's primary branch unresolvable ⇒ every return routes to `REVIEW_REQUIRED`. Needs vendor input — see BLOCKED |
| D5 | `return_source.lkpSearchProduct` holds **1 document** against **482 distinct `masterProductId`** on order lines | Any graph read of the `product` entity resolves for ~0 order lines. **Scope bounded by orchestrator:** `order_line` carries its own `masterProductId`, `productDesc`, `orderQty`, `netPrice`, `invenWhse`, so **Phase 6's order-lines endpoint is NOT blocked.** What is blocked is anything needing product-master attributes |
| D6 | `backend/config/sources/sales_inv.yaml` declares `line_path: salesDtl` / `salesDtlData.lineNumber`; real shape is `salesLines[].salesLnsEventData.*` | Module is `status: DRAFT` and not on the active read path, so harmless today. Stale — separate ticket |
| D7 | Case-aggregate behaviour tests (`test_case_aggregate_real_infra.py`, `test_case_concurrency_real_infra.py`, `test_case_detail_multi_rma.py` — 30 tests) are `live_infra`, deselected by default | The default suite barely exercises the code Phase 2 is changing. **Run the live suite at the Phase 2 integration gate**, on a clean runner |
| D8 | CI step `mypy src tests` reports **394 pre-existing errors across 85 test modules** (0 in `src`) | That quality gate is red for reasons predating this session. Out of scope; flag to the owner |
| D11 | **Platform-wide latent defect, found and fixed by Phase 1.** `bootstrap_graph_configuration.py` carried an active release forward with `ReturnPlatformConfiguration.model_validate(active_payload)`. A configuration key added to `production.yaml` *after* a release was cut is absent from that payload, so it validated to the **model default** and republished the default over the top. **Any new configuration key was structurally unable to reach a deployment that had ever published a release.** | Fixed by a top-level shallow merge `{**packaged, **active_payload}` — release keys still win, keys the release predates come from the packaged file. Two tests. **This is a platform defect, not a Copilot one — it silently defeated every future config addition.** Worth raising with the platform owner separately |
| D31 | **Nothing in the platform ever sets `request.state.tenant_id`** — every reader falls back to `"default"` | So tenant isolation is *implemented* and *tested* but effectively single-tenant at runtime. The route agent had to supply the tenant from a header in a scratchpad wrapper to prove isolation against real `tenant-ops01` data. Not introduced by this programme; worth raising separately |
| D32 | **CLOSED 2026-08-15 (Phase 11).** `mocks/handlers/canonicalHandlers.ts` served the old `CaseDetail` body for `/api/cases/:id`, **including the fabricated `TRK-98421049281`** | Route now serves a real `CaseProjection` (all ten required fields, `additionalProperties: false` clean, `trackingNumber: null` — honest, not invented). The fabricated `TRK-98421049281` / `RMA-2026-78901` survived in the *turns* handler on `isRma`/`isPolicy` keyword branches; both branches deleted. Three independent reasons, any one disqualifying: the numbers were invented; both emitted `GRAPH_FACT` statements citing `qe-1` on turns carrying **no** query evidence, which `HallucinationGuard` refuses outright; and both claimed capabilities (`RMA_ISSUANCE`, `POLICY_EVALUATION`) outside `order-discovery-agent`'s `allowed_business_capabilities`, which `ResponseSafetyGuard` refuses. Nothing was lost — `ReturnCopilotPage` reads no state transition off a turn; the lifecycle moves because the case moved |
| D37 | **FIXED. The sync's `ConstraintValidationFailed` was a real defect in a migration.** `data_platform/graph/migrations/0012_order_discovery_fulltext.cypher` creates four **global, single-property** uniqueness constraints — `Customer.customer_key`, `SalesOrder.sales_order_number`, `OrderLine.order_line_key`, `Product.product_id` — which predate the blue/green generation model and unique-constrain **across** generations. `dynamic_knowledge/graph/constraints.py` states the rule they break in its own docstring: a constraint must cover `graph_generation_id` "or it would wrongly unique-constrain across generations" | Mechanism, measured not guessed: the writer MERGEs on `(graph_generation_id, account_id, sales_order_number)`; in a new generation that matches nothing, so it CREATEs, and the created node sets `sales_order_number`, which the *previous* generation's node already holds. Neo4j reports it without naming the constraint or the property, which is why it read as mysterious. 100/100 `SalesOrder` carried the property; `customer_key` and `order_line_key` were populated on **0** nodes — dead constraints as well as wrongly-scoped ones. Dropped by `migrations/0015_drop_pre_generation_constraints.cypher`. Generation-scoped composites already existed, so no identity guarantee was lost |
| D38 | **`Customer: 20097` was a leaked test fixture, not a keying bug.** 20,000 sat under `graph_generation_id = "fulltext-test-76ddff6056"` — `tests/dynamic_knowledge/test_order_discovery_fulltext_real_infra.py` CREATEs a 20k Customer corpus and deletes it by that generation id in teardown; a run died before teardown. The other 97 were real customers from a FAILED generation | **Functionally harmful, not just untidy:** `customer_name_search_v2` is a **global** index and `operations/associate_flow.py::_graph_candidates` takes the top `candidate_limit` (10) hits from it *before* requiring `PLACED_ORDER`. 20k edge-less junk customers starve every name search. Cleared with the statement the test's own teardown runs |
| D39 | **FIXED by scoping the checks to endpoints, deliberately NOT by stamping the edge.** The justification is load-bearing, not aesthetic: the only writer that would set a stamp (`compile_relationship_writes`) already MATCHes both endpoints inside one generation, so **a stamped edge could never be cross-generation** — while the writer that genuinely *can* bleed, `knowledge/cypher_compiler.compile_relationship_upsert`, matches endpoints on business keys with no generation predicate at all and sets no stamp. A stamp-based check would therefore stay blind to exactly the edges that matter. New predicate: an edge counts if it touches this generation on **exactly one** end, with `coalesce(..., '')` so an endpoint carrying no generation property is reported rather than dropped by null comparison. **Proof the dead check now fires:** against one staged cross-generation edge produced by driving the real `CypherCompiler.compile_relationship_upsert`, old predicate `observed = 0`, new predicate `observed = 1`. Also fixed `_seed_healthy`, which was **stamping its edges** — precisely how the check stayed green in tests while being structurally unable to fire in production. The 14 permanent `RELATIONSHIP_TYPE_POPULATED` warnings dropped to 3. Original entry: **relationships are never stamped with `graph_generation_id`, and this has killed a safety check.** `_compile_relationship_upsert` and `compile_relationship_reconciliation` in `dynamic_knowledge/graph/write_compiler.py` emit `MERGE (a)-[rel:TYPE]->(b)` and never set it. Measured NULL on all 2781 `HAS_ORDER_LINE`, 600 `PLACED_ORDER`, 543 `HAS_CONTACT` | Two consequences. (i) `RELATIONSHIP_TYPE_POPULATED` matches `{graph_generation_id: $generationId}`, so **every one of the 14 relationship warnings is this bug, not sparse data** — it warns for every type on every build and always will. (ii) **`RELATIONSHIP_ENDPOINTS_SAME_GENERATION` is dead.** ERROR severity, docstring calls it "the blue/green failure that is invisible until it matters", filters `WHERE r.graph_generation_id = $generationId` — never true. **It has never once fired.** Fix in flight |
| D47 | **OPEN — full-text reads cannot express the generation inside the index query.** Scoping happens in the `WHERE` *after* `queryNodes` truncates, which is the same hazard `LogicalQueryPlan` refuses ordinary filters for. Mitigated by over-fetching `FULLTEXT_GENERATION_HEADROOM = 10` (capped 2000), so a stale generation must out-rank the live one ten deep rather than once. **This is a mitigation, not a proof** | The complete fix is deleting a generation's nodes at retirement — which is D43: `_retire` only changes the marker's status, so **every generation ever built is still resident in the full-text index**. The two are the same problem seen from opposite ends |
| D49 | **WITHDRAWN — the orchestrator's diagnosis was wrong.** The claim was that activation never reaches the running app because nothing writes Mongo `dynamic_graph_generations`. The premise is true (only the reader and its index builder exist in `src/`; the collection holds 0 documents) but **the conclusion does not follow: that collection is not what readers consult.** `GenerationHandleProvider._resolve` reads `ActiveRuntimeSnapshot` **first** (`handle.py:273-284`) and reaches `MongoGraphStateProvider` only as a *fallback* when no snapshot exists; production wires the snapshot store in at `runtime_factory.py:204` → `targeted_sync.py:159,201`. The snapshot **is** written, by `orchestrator.py:251` (`build_and_activate`'s CAS) and `:530` (`adopt_existing_generation`), and holds `_id: ORDER_DISCOVERY, graph_generation_id: 2ee99fc8-…, activation_version: 2`. **Proven unaided:** built through the real `build_targeted_graph_access` from resolved `Settings` with no generation id anywhere in the harness — legacy resolver alone returns `legacy-live`, the real provider returns `2ee99fc8-…`, leased, marker ACTIVE token 6, and both real Ferguson orders resolve. Read-side resolution and write-side fencing name the same generation, so the `generation.py:35` hazard does not fire | **The 7 leaked ACTIVE markers do not threaten resolution**: it is a single `_id`-keyed read of `{_id: "ORDER_DISCOVERY"}`, not a scan by status — nothing anywhere selects a `GraphGeneration` by status. They will however accumulate forever, because housekeeping's reclaimer only touches generations whose status is exactly `RETIRED`. **The investigation was still worth it** — it found that the legacy fallback was *silent* and its docstring falsely claimed to be "the state of production today", which is exactly how "6,000 nodes in the graph, search finds nothing" becomes unexplainable. The fallback now logs a warning naming what it resolved and why that is only correct pre-cutover. See D50 for the decision it surfaced |
| D50 | **OPEN — needs a decision. On a snapshot *read error* (not absence), `_resolve` degrades to the legacy resolver**, pinned by `test_a_snapshot_read_failure_falls_back_rather_than_failing_the_request` | That was sound while `legacy-live` held the live data. **Post-cutover it degrades to a RETIRED, empty generation — so the degraded answer is now "no results", not "slightly stale results".** A live availability-versus-correctness trade whose sense inverted at the first cutover. Deliberately not changed: reversing it alters failure semantics for every read. Agent's recommendation, and the orchestrator's: **fail closed**. Documented at the site |
| D48 | **`_cdm_parties` in `data_platform/operational_generation/generator.py` hand-builds the old `custAccts` shape.** It is a hardcoded builder, **not schema-driven** as earlier notes assumed | So after D41's correction, seeded customers contribute **0** rows to `customer_account` — which is honest (the real document shape is now what the schema declares) but means synthetic data no longer exercises that entity at all. Outside the graph agent's ownership; queued |
| D40 | **FIXED.** `compile_read` now emits `{graph_generation_id: $graph_generation_id}` on **every** node pattern, not just the start entity — a traversal scoped only at its start hops straight out of its generation on the first relationship. `Neo4jKnowledgeGateway.execute` binds the value instead of `del`-ing it, caller's value winning. **Measured proof of what this was hiding:** unscoped, `CQ363350` and `CW273354` each returned **2 rows** — one from the ACTIVE generation, one from FAILED `73b8b624` — where scoped returns **1**. Before the rebuild the only copy was in the FAILED one, which is exactly what search had been reading. One consequence found and fixed: `api/return_history.py` passed `LEGACY_GENERATION_ID` with a comment explicitly relying on the gateway discarding it, which would now have answered every question from an empty `legacy-live`. Original entry: **the read path is not generation-scoped.** `CypherCompiler.compile_read` emits `MATCH (n0:\`SalesOrder\`)` with no generation predicate; `Neo4jKnowledgeGateway.execute` does `del graph_generation_id`. `handle.py`'s premise — "no code below handle acquisition resolves 'current generation' independently" — is not carried into the query it produces | **This is why order search appears to work.** It reads from a FAILED generation an unscoped reader can still see; the resolved generation prints as `legacy-live` because no `ActiveRuntimeSnapshot` exists at all. Phase 12 is unblocked *in practice* today but **on a false floor** — fixing this correctly returns zero until a generation genuinely activates. Fix in flight |
| D41 | **FIXED against real documents only.** `customerOutboundCDM` holds 301 documents: **300 synthetic** (carry `__context` *and* carry `party.custAccts`) and **1 real** (`MASTER:900781`, no `custAccts` at any level) — so the declared path existed on exactly the 300 fabricated documents. Trap confirmed. New declaration `record_path: [party, partyMainCusts]`, `customer_account ← mainCusts` (`"PLYMOUTH*232385"`), with `customer_id` / `customer_branch_id` split from it by the existing `SPLIT_PART` on `*`. **`ship_to_phone` was removed rather than repointed** — the real bridge record carries no phone, and the party's `additionalMcustomerInfo.mcustPhone` is the *master customer's* number, not a delivery contact; keeping the field name over that value would be a claim the data does not make. Replaced with `customer_name ← mainCustsName`, which is genuinely there. Status `UNVERIFIED → VERIFIED`, `source_access` deliberately left `SEED_ONLY`, `configuration_checksum` re-sealed. Real-document extraction yields `PLYMOUTH*232385` and `MINNWW*28634` (both HIGHVIEW PLUMBING INC; three `partyMainCusts` entries dedupe to two); **synthetic documents now extract 0 rows — honest.** Two `HAS_ACCOUNT` edges formed. See D48 for the seed-generator consequence. Original entry: **`CustomerAccount` declares a path the real data does not have.** `record_path: party.custAccts.additionalCustomerInfo`, `explode: true`; the real CDM document `MASTER:900781` has **no `custAccts` at any level**, so 0 rows extract. The schema half-admits it: `source_contract_status: UNVERIFIED`, `source_access: SEED_ONLY`, description conceding the paths "come from the field specification rather than from data". The real bridge exists under different names: `party[].partyMainCusts[].mainCusts = "PLYMOUTH*232385"` (`BRANCH*CUSTID`) | **The synthetic seed cleared this ERROR dishonestly** — the generator builds `custAccts` *from the declared schema path*, manufacturing exactly the shape the declaration asserts and the real document lacks. Direct vindication of the standing instruction that the schema must match real data rather than the data being padded to match the schema. Fix in flight |
| D42 | **FIXED, and the distinction is a recorded fact about the run rather than an inference.** `_populated_severity(entity, census)` reads a per-source-asset record census threaded down from the build: **census absent → ERROR** (absence of evidence is not evidence of an empty source — pre-existing behaviour preserved); **source scanned, 0 records → WARNING**; **source scanned, N > 0 records, zero nodes → ERROR, unchanged** (the build read the source and lost every record — broken `record_path`, unresolvable natural key, a projection that dropped the lot); **source not in the census at all → ERROR**. **The load-bearing detail:** "scanned and empty" and "never scanned" were *not* distinguishable — an empty collection yields no pages, so it was simply absent from the counts. `_CountingConnector.scan` now does `setdefault(source_asset_id, 0)` before the first page, recording participation explicitly. Without that the agent would have been guessing and was instructed to stop instead. Proven by a real-Neo4j test that validates **the same empty generation twice** — passing under `{all: 0}`, failing under `{all: 250}` — so the distinction demonstrably cannot come from the graph. **But the first version of this fix WAS the weakening the brief warned against, and the cutover suite caught it.** `test_a_failed_candidate_leaves_n_active_and_still_serving` induces failure by **dropping the source collection** — which the census reads as "source legitimately empty" → WARNING → **an empty generation activated over a populated one** (`DID NOT RAISE ActivationError`). The census genuinely cannot separate a dropped collection from a never-populated one: both scan as zero. So the distinction was taken from where the evidence actually exists — **the generation currently being served.** New ERROR check **`NODE_LABEL_REGRESSED`**: if the generation this candidate would replace holds nodes of a label and the candidate holds none, activation is refused *whatever the source says about why*; emitted only when a predecessor exists, so a first build still bootstraps. **The two halves only work together** — the census WARNING is what lets a fresh deployment activate at all, the regression guard is what stops an emptied source replacing live data. Neither is safe alone. Predecessor plumbed through validator → `compile_validation_checks`; the orchestrator reads the serving snapshot *before* validating. Original entry: **node-label validation severity is hardcoded, making the gate unbootstrappable.** `ReturnItem` / `ReturnHandlingUnit` are ERROR-refused for being empty, but they come from `operational_return_items` / `handling_units` — platform stores written when an associate selects order lines and by the packing workflow, legitimately 0 documents on a fresh deployment | So **a deployment that has never processed a return can never activate a generation, and can never process a return, because that needs order search.** Note the asymmetry: `RELATIONSHIP_TYPE_POPULATED` is only a WARNING, reasoned as "failing over an empty result would make the platform unable to rebuild at all" — which applies verbatim here. Fix is to derive severity from the run: WARN when the source asset yielded zero records, ERROR when records were read and none projected. **That distinction must stay real** — "the build lost a slice of the domain" remains a hard ERROR |
| D43 | **Nothing in the platform ever reclaims a failed or retired generation's storage.** `orchestrator._retire` is a status transition (`ACTIVE → DRAINING → RETIRED`) and never deletes nodes | Which is why **235 `GraphGeneration` nodes** had accumulated on this stack. Cleanup is currently manual Cypher. Not a Copilot defect; worth raising with the platform owner separately, alongside D11 and D31 |
| D35 | **`POST /api/config/releases/{id}/promote` has two different 422s and OpenAPI can only describe one.** A body missing the required `PromoteReleasePayload.status` literal is refused by FastAPI before the route runs → `HTTPValidationError`, `detail` as a **list**. But `promote_configuration_release` also raises `ReleasePromotionError(...)` → `detail` as a **string**, and a hand-raised `HTTPException` detail is not part of the generated schema | The mock handler served the *string* form unconditionally — answering a body the document contradicts, to a request that could never have produced it. Handler now branches on which refusal the request actually earns. **No spec edit made and none needed**, but this is a real gap between the generated document and the route's behaviour, and no contract test can ever catch that class |
| D44 | **`POLICY_EVALUATION` is two different vocabularies sharing a spelling.** It is a legitimate `CopilotStage` (`operations/case_projection/vocabulary.py:106`, plus `api/cases.ts`, `domains/returns/types.ts` and the generated `.d.ts`) **and** was live as a `business_capability` value at `ReturnCopilotReload.test.tsx:377`, where `CapabilityGuard` would have refused it | Neither side is typed against the other: `business_capability` is a plain `str` at `dynamic_knowledge/knowledge/evidence.py:83` and `order_agent/contracts.py:152`, so **the backend has no type-level protection either** — only `guards.py:305`'s runtime membership check. A stage name is a plausible-looking capability and vice versa; nothing but D36's new guard stands between them. Corrected to `return-context-collection` (the test's actual lever is `status: "APPROVED"`; assertions untouched) |
| D36 | **CLOSED 2026-08-15.** Guard written: `backend/tests/test_console_mock_speaks_the_agent_policy.py`. Original entry: **the frontend contract test cites a guard that does not exist.** `canonicalHandlers.contract.test.ts` states the `business_capability` vocabulary is checked by `backend/tests/test_console_mock_speaks_the_agent_policy.py`. **That file is not in the repo** | OpenAPI types `business_capability` as a plain `str`, so the contract test structurally cannot check its *value* — which is how `order_discovery` (underscore) survives in `modeFixtures.ts:44` and ~8 inline test literals against the real hyphenated `order-discovery`. **This is the same mismatch that already 422'd every turn once** (see `bootstrap/api.py:48`). **10 wrong literals found** (1 fixture + 8 in `ReturnCopilotPage.test.tsx` + 1 in `ReturnCopilotReload.test.tsx`), all corrected to `order-discovery`. Authority is `active-schema.return-order.yaml`, established from `settings.py:18`'s `DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH`, `.env.example:205`, and ~30 backend tests loading it as the shipped schema (`active-schema.example.yaml`'s shorter list is a loader fixture used by exactly one test). **The guard parses frontend source rather than importing a shared constant** — deliberately, because a shared constant only covers occurrences that opted into it, and the eleventh inline literal is how all ten current ones were written. Two things keep it honest: a **vacuity floor** (≥6 literals in ≥2 files, asserted first — a source-parsing guard that silently matches nothing is a green check that stopped checking), and a **teeth test** pinning `order_discovery` / `POLICY_EVALUATION` / `RMA_ISSUANCE` as non-members. Verified by reinjection. Also fixed the type layer that made the wrong shape plausible: `api/orderAgent.ts` gains `EvidenceReference`, `evidence_refs: EvidenceReference[]`, and the two required `QueryEvidence` checksums — 8 resulting `tsc` errors were all in test files, **no component needed changing** (`ReturnCopilotPage.tsx:53` reads only `evidence.result`) |
| D29 | **`WAREHOUSE_RECEIVING` is unreachable and there is no producer to wire.** Beyond the four receipt fields having no case-keyed writer, the only case-level fulfilment producer is fact `fulfillment_status`, drawn from `FulfillmentTrackingStatus`, whose **complete** membership is `NOT_APPLICABLE · AWAITING_HANDOFF · IN_TRANSIT` | **Nothing in the platform can say a return arrived.** Solving shipment attribution would still not reach the stage. A test asserts this against the richest fact log the workflow can produce, plus a disjointness check — **it fails the day a receiving producer lands**, which is the day to wire it |
| D30 | `backend/scripts/export_openapi.py` raises `PydanticUserError: APIResponse[CaseDetail] is not fully defined` when run standalone, though it succeeds under pytest | In the concurrently-edited `api/cases.py` import graph. Watch at integration — the export script is what regenerates the snapshots |
| D27 | **My brief named five revision writers; two live in a file I had excluded.** `update_return_item` and `assign_return_item_to_record` are in `operations/repository.py`, not `case_repository.py`, and `OperationalRepository(CaseRepository)` shadows the mixin — so they could not be fixed from the assigned file | **They still violate §6.5, and `assign_return_item_to_record` is live** — `return_case_activities.py:868` calls it on every Support outcome that maps order lines to an RMA. Orchestrator error, not the agent's. Queued |
| D45 | **A strict "no line assignment without an authorized reservation" rule is unshippable, and the orchestrator brief that demanded one was wrong.** `item_reservation_ttl_seconds` is **1800 (30 min), wall clock**; `support_response_wait_seconds` is **28800 (8 h) on the business calendar** (Mon–Fri 09:00–17:00 America/New_York). Eight business hours against a 9–17 week is one full working day — a return raised 16:30 Friday reaches its Support deadline ~16:30 Monday, roughly **72 wall-clock hours against a 30-minute hold**. Widening the TTL is explicitly rejected by its own config rationale: a hold surviving a weekend makes Monday's associate wait on Friday's abandoned conversation | Under a strict rule the sequence is: hold lapses at `t0+30m`; Support answers at `t0+~4h`; `record_support_outcome` commits the RMA to the authoritative SQL store first, then `authorize_reserved_line` matches nothing → `QuantityReservationExpiredError`; the item assignment shares that transaction so **the item stays unattached**; `_PERSIST_RETRY(maximum_attempts=5)` burns five identical attempts and the workflow fails — over a hold that expired hours before Support ever replied, with the RMA in SQL and nowhere on the case. **Rule actually implemented:** *a live hold is always consumed by the assignment that supersedes it, in one transaction* — `_assign_items_to_record` falls through to plain `assign_return_item_to_record` when no live hold exists, logging `order_line_hold_settled_before_authorization`. The double-count cannot return because `case_line_holdings` partitions each `(case, line)` onto exactly one contributor: an item with a `returnRecordId` wins unconditionally, and a reservation failing `is_held` is skipped. Proven on real Mongo against the *arithmetic*, not the state |
| D46 | **OPEN — needs a decision. Units are genuinely unprotected between hold expiry and Support's reply.** §12.2's formula counts only `ACTIVE` holds and RMA-covered items, so in that window a second case can select and be authorized for the same units, and **total authorized can exceed the quantity ordered**. Pre-existing; not introduced by the Phase 6 work | Closing it needs either a TTL spanning the Support wait — which D45 shows the TTL's own rationale rejects — or **a fourth term in the availability formula for "selected but not yet authorized"**. The formula was deliberately not changed. This is a real overselling hole, not a theoretical one |
| D28 | **CORRECTED — the diagnosis below was right that `open_case_thread` was broken, and wrong about why.** The projection **cannot** observe its torn state: `load_case_projection_state` reaches the work item only via `_load_support_work_item(case)` (`case_repository.py:611`), which reads `case["channelBWorkItemId"]` and returns `None` without touching `support_work_items` when absent — and `open_case_thread` never writes that link; `open_support_work_item` (`return_case_activities.py:841-849`) does, afterwards, through `update_case`, which bumps on its own. So §6.5 does not apply and **no revision bump was added** (adding one would invalidate every cached projection for a document no client can yet reach). **The real defect is durability:** the two inserts ran outside any transaction, so a failure between them leaves a work item with `caseId` set and no message — whereupon the idempotency guard (`$or` on `idempotencyKey` / `caseId`) returns that work item on every later call **including every Temporal retry**, so the opening message can never be written. **Support opens the conversation it is meant to answer and finds it blank, permanently.** Fixed by running both inserts in one `with_transaction`. Original ranking follows: | **`apply_action` is required** (sets `status`/`completedAt`/`assignedTo`, three of them projected, on an already-linked case); `open_case_thread` is conditional (covered today only by the separate `update_case` link write, and its two inserts sit outside any transaction); `add_message`/`post_reminder` not required until the projection grows a message field; `create_work_item` never (session-keyed) | `bump_case_revision` is public precisely so both files enrol it in their own transaction in one line. Queued behind Phase 4 |
| D25 | `claim()` does a **blocking in-memory sort**: `status: {$in: [PENDING, RETRY]}` on the leading key stops `status_1_nextAttemptAt_1` providing sort order, so the server sorts every matching command in memory | Pre-existing, unrelated to the index drift, out of scope — but it will bite as the outbox grows. Fixing it means either two indexed lookups or an index the workers must agree on |
| D26 | `leaseOwner` is unindexed, so the `claim()` lease `$or` degrades to a residual FETCH filter | Looks deliberate (a worker reclaiming its own lease is the rare branch) but nothing records the decision. `leaseUntil_1` **is** proven index-backed for the lease predicate queried alone |
| D22 | **CLOSED 2026-08-15 — all six writers now bump, proven concurrently on real MongoDB.** The lost-update test was extended in place (`test_eight_concurrent_writers_across_four_collections_lose_nothing` → `test_every_projection_writer_contends_at_once_and_loses_nothing`), **8 → 10 contenders** released from one `asyncio.Barrier` across four collections: `apply_action` (D28), `assign_return_item_to_record` ×3, `update_return_item` *(direct — previously reached only via assign)*, `append_case_fact` ×2, `update_return_record` *(newly in the window)*, `create_case_return_item` *(newly in the window)*, `create_return_record`. Revision advanced by **exactly 10** with `updatedAt` strictly forward, no writer raised, every child write landed. Branch labels asserted as a set, so an index drifting after a later edit fails loudly rather than silently narrowing coverage; a read-modify-write repair fails it two ways at once. **D27's two writers verified at runtime off the concrete class, not the mixin** — both absent from `CaseRepository`, `self.return_items` bound in `OperationalRepository.__init__`, MRO `['OperationalRepository', 'CaseRepository', 'object']`; `assign_return_item_to_record` carries no bump of its own *by design*, inheriting it by delegating to `update_return_item` so the two cannot come to hold the invariant differently. Original entry: **§6.5's revision invariant is violated today.** `cases.version` exists, is monotonic and optimistic-concurrency-checked — but `append_case_fact`, `create_return_record`, `update_return_record`, `create_case_return_item`, `update_return_item` and the Support work-item writers all change the projection **without** bumping it. Only the policy override bumps it deliberately | The client's stale-response guard is therefore **decorative** for everything except an explicit case-document write. Mitigated in the meantime: the assembler reads the case document *first* and children after, so the reported revision can only be **older** than the children — the client discards and re-polls rather than accepting a fresh revision over stale children and never looking again. **Real fix outstanding:** every child writer must bump `version` + `updatedAt` in the same transaction. Next task once `return_support/service.py` frees |
| D23 | **`returnMethod` has no persistence in the case aggregate.** No column on `ReturnRecordView`, no writer records it as a fact; it lives on the legacy `ReturnSessionView.approvedReturnMethod`, which Copilot cases never have | So every case projects `returnMethod: None`, the completion profile never resolves, and **`businessComplete` can never become true**. Honest, not a bug — but it means the lifecycle cannot complete until a writer records it. The assembler already falls back to case facts `approved_return_method` / `return_method`, and with that fact present an approved `PREPAID_PARCEL` case with RMA+label+tracking projects `stage=AUTHORIZED_RMA, awaiting=(), businessComplete=True`. Spec in flight |
| D24 | **A label without a shipment was inexpressible.** `ReturnArtifactProjection` was reachable only via `ShipmentProjection.labelArtifacts`, so record `4e372a39…` (RMA + label + null tracking — the exact stuck state the audit found) had a derivable artifact and nowhere to put it | **Orchestrator ruling: the contract was wrong, not the data.** Artifacts move to the return record with an optional `shipmentId`; minting a synthetic shipment to carry the label would have been the `TRK-98421049281` fabrication in a new costume. Fix in flight |
| D20 | **Chicken-and-egg on the stored release.** `return_method_requirements` is now required with no Python default, so the active Neo4j release — which predates the key — no longer validates. Dev falls back loudly to the version-controlled baseline; **production refuses to start**. Republishing first would break the currently-deployed old container, since `StrictConfigModel` is `extra="forbid"` | **Republish and rebuild must happen together in one integration pass.** Not a defect in either change; a deployment-ordering constraint. Deliberately not republished |
| D21 | **CORRECTED — "unreachable today (no production caller)" was FALSE, and this is live.** `workflows/return_case_activities.py::_assess_completion` (~line 1265) calls `project_case(state)` **with no table**, and `projection.py::project_case` defaults `requirements` to `DEFAULT_RETURN_METHOD_REQUIREMENTS`. **So the workflow's decision about whether a case keeps waiting or closes is already computed from the code constant while the API answers from the released configuration.** They are identical row-for-row today, which is why nothing has diverged — but the first operator edit to `return_policy.return_method_requirements` makes the workflow and the API disagree about the same case | Defaults removed from `resolve_completion` / `resolve_method_requirements` — **no default rather than a raising sentinel**, because the codebase is mypy-strict so a required keyword-only parameter fails at type-check *and* runtime, whereas a sentinel only fails when the path executes, and this path executes on every case read. The constant is kept and re-documented as explicitly **not** a default: it is load-bearing as the "table known not to be the released one" that `test_case_projection_route.py` uses to prove the route answers from configuration. `project_case`'s own default deliberately left for a second pass — removing it without fixing the caller would be **actively harmful**, since `_assess_completion` wraps its body in `except Exception` by design, so the `TypeError` would be swallowed as `case_completion_not_assessable` and **every case would wait forever behind a warning log**. Caller fix in flight |
| D33 | ~~**This stack's knowledge graph holds zero `SalesOrder` nodes.**~~ **CORRECTED 2026-08-15 — the original entry was measured before the first build attempt and is wrong.** The graph is *not* empty: `Customer 20097 · OrderLine 572 · CustomerParty 302 · CustomerAccount 300 · ContactPoint 298 · GraphGeneration 235 · SalesOrder 100 · Shipment 94 · GraphWriteReceipt 26 · ConfigurationDomain 12 · Bay 12 · ReturnCase 9` — 22,082 nodes, 22 constraints. A build **has** run and projected the real orders and lines; it was refused at *activation*, not at projection | **The real defect is the sync itself, not a seeding gap.** Attempt 1 was refused with `NODE_LABEL_POPULATED[CustomerAccount\|ReturnItem\|ReturnHandlingUnit]` + ~12 `RELATIONSHIP_TYPE_POPULATED[...]` — which against *real* data most likely means **the declared graph schema and the real extract disagree**, not that rows are missing. Attempt 2 failed harder, with `ConstraintValidationFailed` on MERGE — **root cause found, see D37**. `Customer: 20097` — **explained, see D38**. **RESOLVED 2026-08-15, end to end** (D49's contrary claim was withdrawn — the runtime resolver does find it, proven unaided). Generation `2ee99fc8-9b15-4747-9eba-a7b61fc88180` is ACTIVE** (fencing token 6, 12,966 node writes, 16,936 relationship writes, `legacy-live` RETIRED, `cutoverStage RETIRE_PREVIOUS`); all 52 compiled checks re-run against it give **0 errors, 5 warnings** (the two legitimately-empty platform-written labels and the three relationships depending on them). Order search through the unmodified real path — `GenerationHandleProvider` → `CypherCompiler` → `Neo4jKnowledgeGateway`, generation leased — returns `CQ363350` (CHARLOTTE / ATLAS MECHANICAL SERVICES, 1 order line) and `CW273354` (OHVAL / MELGON HEATING & COOLING, 2 order lines). Root causes were D37/D39/D40/D41/D42, not a seeding gap. **Standing correction to scope: the objective is that the sync runs clean and the schema matches the real data — not that enough rows exist to satisfy the validator.** Orchestrator error: 500 synthetic orders / 800 products / 300 customers were seeded additively to make the gate satisfiable, which risks masking the mismatch; the 101 real Ferguson orders (`_id` shaped `BRANCH*ORDERNO`) are the corpus that must work, and were preserved. Under investigation |
| D34 | **FIXED 2026-08-15 (orchestrator).** `ProviderRequest.task_id: str \| None = None` added; `final_dispatch.py` sets it from the `DispatchRequest.task_id` it already held; `durable_interception.py` reads `request.task_id or self._task_id` — the request wins, construction-time is the fallback for callers predating the field. Two tests: the held record names the invoking task, and a request without one still falls back to the route's. 772 AI/gateway/provider tests green. Original entry: **every held MANUAL request showed `taskId: "UNKNOWN"` in the operator queue.** `routes.py::_manual_provider` constructs `DurableInterceptionProvider(settings, interception_store)` with no `task_id`, and the provider is built once per *route* at `build_routes` time — so it cannot know which task a given request belongs to | One MANUAL route serves both tasks that permit it, and `ORDER_AGENT_REASONING_V1` (an `AgentAction`) and `GRAPH_SCHEMA_PROPOSAL_V1` (a graph proposal) want completely different JSON. The **payload** is sufficient to answer either — `systemPrompt` carries the required response schema — so this is queue legibility, not a blocker. Fix is one field: `ProviderRequest.task_id: str \| None = None`, set from the `DispatchRequest.task_id` the dispatcher already holds, read as `request.task_id or self._task_id`. Left alone deliberately: it touches `ai/gateway/final_dispatch.py`, outside this task's assigned files |
| D16 | **The running backend container is stale and 500s on `GET /api/cases/{id}`** — `AttributeError: 'OperationalRepository' object has no attribute 'get_case'`. The image holds the post-extraction `repository.py` **without** `case_repository.py`; a rebuild landed mid-extraction | **Deployment artifact, not a source defect** — verified locally: the app factory imports and all 17 methods resolve through the mixin. Rebuild deliberately deferred: 3A.7 is mid-edit on the workflow file and baking that risks a broken container. Rebuild + OpenAPI regeneration happen together as one integration pass |
| D17 | Deployed images predate 3B, so `integration-outbox-worker` has **no `TemporalSignalDispatcher` registered** — proof commands landed at `BLOCKED_EXTERNAL_DEPENDENCY / ADAPTER_NOT_CONFIGURED` | Same rebuild closes it. **Gate 3B's delivery half cannot run until then** |
| D18 | **Outbox index definition has already drifted three ways.** `repository.ensure_indexes` builds `idempotencyKey` + `(status,nextAttemptAt)`; `IntegrationOutboxDispatcher` adds `leaseUntil` (behind its own `claim()` lease predicate); `ReturnSupportService` adds `(createdAt DESC)` (behind the operator listing's sort). **No owner creates the union** | So the obvious cleanup — "delete the redundant third copy" — would silently turn the operator listing into a collection scan plus in-memory sort. Recommendation: `operations/integrations/outbox.py` owns a shared `ensure_integration_outbox_indexes(database)`, following the precedent 3B already set with `ensure_support_event_indexes`. Deferred — `main.py` is locked |
| D19 | Test data written to shared Mongo by the 3B runtime proof: 2 support events + 2 outbox commands on case `d3190045-…`, RMAs `RMA-PROOF-1755239348` and `…-B` | Harmless; flagged so a later agent does not mistake it for production data |
| D13 | **Persisted `CaseStatus` and the new `ReturnCaseStatus` are different enums.** `operations/models.py:108` (mirrored at `return_case_workflow.py:99`) is the older set: `GATHERING_INFO · AWAITING_BAY · AWAITING_SUPPORT · RMA_RECEIVED · IN_TRANSIT · CLOSED · CANCELLED` | §6.7's backfill needs an explicit mapping — `AWAITING_BAY`/`RMA_RECEIVED`/`IN_TRANSIT` → `PROCESSING_RETURN`, `CLOSED` → `COMPLETED` or `COMPLETED_EXTERNAL_SETTLEMENT` by settlement. **Reconciliation deliberately deferred** so it does not collide with the policy gate now editing the workflow |
| D14 | **`npx --prefix frontend vitest run` is the wrong invocation.** `--prefix` moves npm's bin resolution but not the cwd, so vitest runs from the repo root and sweeps all 14 worktree mirrors: 367 files / 1179 tests, 282 failing with `window is not defined` | A spectacular false red. Correct form is `cd frontend && npm run test`. Corrected in all later agent briefs |
| D15 | Plan §6.4 says align `awaiting` with `workflow.completion_dimensions`. Those are `customer_resolution_complete · physical_return_complete · warehouse_processing_complete · vendor_recovery_complete · case_fully_closed` — **no shared vocabulary** with §6.4's `RMA/LABEL/TRACKING/BOL/PICKUP/RETURN_LOCATION` or §7.6's `WARRANTY_VERIFICATION` | **Orchestrator decision: the §6.4/§7.6 vocabulary wins.** `completion_dimensions` is the *session* workflow's notion of done and the Copilot does not run on sessions. The plan's "align" instruction was wrong; `AwaitingDimension` stands as its own closed enum |
| D12 | The compose bootstrap runs `--if-missing`, which short-circuits when an active release exists | So D11's fix does **not** self-apply on an existing deployment — republishing is a manual deployer step. Semantics deliberately unchanged |
| D10 | **~15 stale `.claude/worktrees/agent-*` worktrees**, all at older commits on their own `worktree-agent-*` branches | Not an integration risk — this session's agents write to the main tree (verified). But they **triple-count every repo-wide grep**: 3A.1 hit 40 matches of which 32 were worktree mirrors of 8 real files. All later agent prompts must scope searches to `backend/src`, `backend/tests`, `backend/config`, `frontend/src`, `docs`. Not deleted — they may hold unpushed work from earlier sessions; that is the owner's call |
| D9 | Three `test_openapi_contract_drift.py` failures — one of four committed OpenAPI snapshots regenerated, three stale | Transient integration artifact of Phase 1. Orchestrator regenerates the remaining three once Phase 1 lands |
| D2 | No reasoning provider on this host | Blocks UI-driven gates only |
| D3 | 6 cases read `AWAITING_SUPPORT` with TERMINATED executions | Real orphan data for Phase 10 to reconcile |
| D4 | Real record `4e372a39…` has RMA + label, `trackingReference: null`, workflow COMPLETED | The exact stuck state Phase 4 must make impossible |

---

## BLOCKED

| Blocker | Attempted | Needs |
|---|---|---|
| ~~Seller restocking-fee schedule~~ | **CLOSED for dev 2026-08-15 by operator decision.** "Put a random amount, it's dev not live" | `1500` bp (15%) stands in `production.yaml`, arbitrary and marked as such, attributed `SELLER_CONFIGURATION` so it can never read as published Ferguson policy. **Still needs a real figure before any live deployment** — the decision settles this environment only |
| ~~**`lineType` / `prodStatus` code-list definition**~~ | **CLOSED for dev 2026-08-15 by operator decision.** "Check for real data, if not found skip special items as of now." Re-confirmed: 3A.1 had already exhausted every source field, all four vendor dictionaries, and SQL Server — there is nothing further to check | `stock_classification.unresolved_default: STANDARD_STOCK` with all three designation lists empty, i.e. **special-order handling is switched off**: unclassified lines are treated as ordinary stock and flow normally. Every decision taken this way carries `STOCK_CLASS_FROM_CONFIGURATION`, so an approval made on the assumption is auditable as one and never reads as evidence. Reversible in one line if Ferguson later supplies the code list — see below for why the data cannot answer it |

### Why `line_type` cannot be mapped without the vendor's word

Observed domain (orchestrator-verified against `return_source.salesInv`, 572 lines):
`MP` 499 · `C` 39 · `CB` 24 · `SP` 8 · `NA` 1 · `F` 1.

Three independent reasons, any one sufficient:

1. **The negative case is false.** Approval requires asserting `seller_stocked = true`. 89 of the
   499 `MP` lines carry `taggedPoId` with `prodAvailQty == 0` — catalog products bought in against
   a specific PO, i.e. procure-to-order, sitting exactly on Ferguson's "normally stocked" seam.
   Mapping `MP ⇒ stocked` would convert 89 unknowns into approval evidence, which is the failure
   plan §7.3 exists to prevent.
2. **The token is overloaded in the same records.** `lineData.origProdStatus == "SP"` occurs 8
   times, only 4 of them on `lineType == "SP"`; the other 4 are ordinary numeric catalog products.
   The vendor dictionary defines the sibling `whseProducts[].prodStatus` as warehouse *activity*
   status, not stock class.
3. **No authority defines the domain.** None of the four vendor documents lists `lineType` at all.

**Cheapest unblock (recommended):** a written Ferguson code list for `lineType` and
`prodStatus`. If it confirms `SP` = special order *and* that `MP` is silent on stocking, then
`line_type` becomes usable as a **positive-only** signal — `SP ⇒ special_order = true`, everything
else stays `UNKNOWN` — which makes 8 lines decidable without reason (1). Alternative, larger:
extend the `lkpSearchProduct` extract with Trilogie's stocking class on `whseProducts[]` and
populate the collection beyond one row; `(masterProductId, invenWhse)` already addresses it, so no
new relationship is needed.

Nothing else is blocked on user input, and nothing is waiting on this — every other phase proceeds.

**Operator decision, 2026-08-15:** special-order handling is **off** for now
(`unresolved_default: STANDARD_STOCK`, empty designation lists). The three reasons above are
unchanged and the data still cannot answer the question; what changed is that the environment is
dev, so treating unclassified lines as ordinary stock is acceptable there. The reversal is one
YAML key. **This must be revisited before live** — with the default at `STANDARD_STOCK`, a line
that really is a special order is approved as though it were stock, which is precisely the failure
reason (1) describes, now accepted knowingly rather than unknowingly.

---

## Wave log

### Wave 1 — in flight

| Slot | Task | Scope |
|---|---|---|
| 1 | Phase 1 — runtime agent mapping | config + `bootstrap/api.py` + `runtimeConfig.ts` + `ReturnCopilotPage.tsx:185` |
| 2 | 3A.1 — source `special_order` | **DONE — outcome (c), no schema change.** `tests/dynamic_knowledge tests/configuration` 703 passed. No file modified. Slot refilled with Phase 2 extraction |
| 3 | 3A.2–3A.5 — deterministic evaluator | all new files; no existing file touched |

Contention avoided: slot 3 was told **not** to edit `return_configuration.py` (slot 1 owns it);
root-model wiring is done by the orchestrator at integration. Phase 2's `case_repository.py`
extraction is held until the test baseline completes, so a mid-run move cannot corrupt
attribution.
