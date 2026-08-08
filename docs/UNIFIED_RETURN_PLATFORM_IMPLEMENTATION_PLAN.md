# Unified Returns Platform — Implementation Plan

**Status:** Ready for execution
**Planning baseline:** `c3cdd354fdef93583c2b67da219701e76489a221` (verified = current HEAD, working tree clean)
**Source branch:** `feat/v2-order-discovery-integration`
**Implementation branch:** `refactor/unified-return-platform`
**Authored:** 2026-08-07

This plan supersedes the draft consolidation plan. Every "current state" claim below was verified against the
tree at the planning baseline; file/line references are real. Where the draft plan's assumptions were wrong,
the correction is called out inline under **Correction**.

---

## 1. Product target

Exactly four user-facing product domains:

| Route | Domain |
|---|---|
| `/returns` | Return Business Copilot |
| `/config` | Configuration |
| `/graph-schema` | Graph Schema Analyzer |
| `/ai` | AI Control Center |

Nothing user-facing named V1, V2, Data Console, Data Studio, AI Studio, Sandbox, Dependency Simulator,
Workspaces, Scenarios, Operational Generation, or Seed Data administration. Development and test utilities may
survive internally, but never as production navigation, production bootstrap dependencies, or application domains.

---

## 2. Verified current state

These are the facts the plan is built on. Re-verify before starting if the branch has advanced.

### 2.1 Backend

| Fact | Evidence |
|---|---|
| 36 routers registered at startup | `backend/src/return_platform/main.py:880-915` |
| API prefixes are fragmented across 4 namespaces | `/api/v1/*`, `/api/v2/*`, `/data-console/v1/*`, `/api/v1/data-console/*` |
| Two agent registries exist | `agents/registry.py` (concrete, 6 agents, no indirection) and `dynamic_knowledge/agents/registry.py` (`IndependentAgentRegistry`, descriptor-only, enforces unique `agent_id`/`task_queue`/`state_namespace`) |
| `backend/config/v2` **is loaded at runtime** | `main.py:369` → `V2PlatformServices.bootstrap()` → `ModularConfigurationService.bootstrap()` (`v2/services.py:136`) |
| …but every module loads as `status: DRAFT` and nothing consumes it | `v2/services.py:157` hardcodes `ModuleStatus.DRAFT`; no activation path |
| A working system-store bootstrapper already exists | `dynamic_knowledge/internal_store/` — `InternalStoreAdapter` protocol + `mongo_adapter.py`, `neo4j_adapter.py`, `sql_adapter.py`, and `bootstrap.py` implementing inspect → MISSING-create / INCOMPATIBLE-raise / COMPATIBLE-reuse / `ensure_indexes` |
| Schema design sessions are in-process | `v2/services.py:647` — `self._contexts: dict[str, SchemaDesignContext] = {}` |
| …but the namespace *is* snapshotted to Mongo | `V2PlatformServices.bind_state_store` / `persist_all` (`v2/services.py:1111-1141`) with optimistic revisions, whole-namespace blob |
| Manual AI provider is filesystem polling | `ai_gateway/providers/manual.py:27,82` — `.manual_llm` dir, `asyncio.sleep` loop |
| Generation lifecycle orchestrator is built and live-validated but **not wired to any caller** | `dynamic_knowledge/lifecycle/orchestrator.py`; only referenced from `backend/tests/dynamic_knowledge/test_lifecycle_orchestrator.py` |
| Generation read leases / draining are absent | `lifecycle/orchestrator.py:8-15` (module docstring states this) |
| 1133 backend test functions across 92 files | `grep -rn "def test_" backend/tests \| wc -l` |

### 2.2 Frontend

| Fact | Evidence |
|---|---|
| Real V1/V2 split at the shell level | `frontend/src/App.tsx:76-88` |
| Everything not under `/v1` or `/v2/...` is **redirected** to `/v1/...` | `App.tsx:85-87` + `versioning.ts:legacyRouteDestination` |
| 74 routes, 40 of them `/data-console/*` | `frontend/src/routes.ts` |
| Feature module sizes | `data-console` 67 files, `operations` 15, `dependency-simulator` 9, `copilot-v2` 2, `configuration-v2` 1, `data-source-config` 1 |
| RBAC is a stub | `backend/src/return_platform/security/principal.py` defines only `Principal` and `AuthorizationError` |
| 24 frontend test files + Playwright configs | `playwright.config.ts`, `playwright.real.config.ts` |
| OpenAPI artifacts are committed in three places | `openapi.json` (272 KB), `backend/openapi/return-platform.openapi.json`, `frontend/openapi/return-platform.openapi.json`, plus 13 hand-maintained files in `frontend/src/contracts/` |

### 2.3 Infrastructure

| Fact | Evidence |
|---|---|
| SQL migrations 003 and 004 exist but **are never applied** | `compose.yaml:203-204` runs only `001` and `002`; `infra/sqlserver/init/` contains `003_production_return_platform.sql` and `004_production_bay_constraints.sql`, referenced only from `docs/evidence/` |
| `backend` blocks on seed tooling at startup | `compose.yaml:404` — `depends_on.seed-runner: service_completed_successfully`; identical on `return-workflow-worker` and `return-orchestrator` |
| All application services sit behind a Compose profile | `profiles: ["containerized-app"]` on every app service — default `docker compose up` starts infrastructure only |
| Two outbox services exist | `outbox-publisher` (`compose.yaml:441`) and `integration-outbox-worker` (`compose.yaml:463`) |
| The config bootstrap service is named `runtime-configuration-init` | `compose.yaml:364` (the draft plan called it `runtime-bootstrap`) |
| SQL Server is a first-class service | `compose.yaml:155` `sqlserver` + `:178` `sqlserver-init` (omitted from the draft plan's target topology) |
| Neo4j migrations live in the package | `data_platform/graph/migrations/*.cypher` (0010–0014), applied by `scripts/apply_neo4j_migrations.py` |
| A live, seeded Docker stack is available | project `return-multi-agent-platform`; Mongo `127.0.0.1:27017` (`return_source` has ~1000-doc Ferguson-shaped collections), Neo4j `bolt://127.0.0.1:7687`, SQL Server `localhost:14330` |

---

## 3. Decisions taken

These resolve ambiguities in the draft. Each is a judgement call — challenge any of them before the phase that
depends on it, noted in brackets.

**D1. `backend/config/v2` is promoted, not deleted.** [affects P2, P3, P5, P6, P26]
It already contains a `platform/system_store.yaml` manifest, a `workflows/return_session.yaml` stage list, and
eight agent descriptors — the closest thing in the repo to the target configuration model. The draft plan
invented these from scratch in P2/P3/P5/P6 and deleted the originals in P25. Instead: the content moves to
`backend/config/` (version namespace dropped), and the work is writing loaders that actually *consume* it.
Today it is parsed into memory as `DRAFT` and nothing reads it.

**D2. `dynamic_knowledge/internal_store/` is the seed for `platform/system_store/`.** [affects P3]
The draft plan said "do not pretend unsupported relational system-store implementations exist" — but Mongo,
Neo4j, and SQL adapters all exist and work, and the bootstrapper already implements the target startup
algorithm. Mongo remains the canonical provider; the other adapters stay as proven implementations of the
port. The real gaps are locking, schema-version history, and logical→physical name resolution.

**D3. `outbox-publisher` survives; `integration-outbox-worker` is retired.** [affects P25]
`outbox-publisher` runs `scripts/run_outbox_publisher.py` and depends on the runtime-config init;
`integration-outbox-worker` runs `return_platform.workers.integration_outbox`. Confirm which is authoritative
in P25 by comparing behaviour — if they are not equivalent, consolidate rather than drop.

**D4. Six agents, not eight.** [affects P5, P6]
`config/v2/agents/` holds eight descriptors. `return_session_orchestrator.yaml` becomes the orchestrator
configuration (the orchestrator is not an agent — it invokes agents), and `graph_schema_design.yaml` becomes
the Graph Schema Analyzer's AI-task configuration (the Analyzer is a module, not a workflow agent). The six
workflow agents are Order Discovery, Order Analysis, Return Workflow, Return Fulfillment, Bay Assignment,
Feedback Learning.

**D5. This plan absorbs the remaining Dynamic Source-to-Graph Alignment steps.** [affects P8, P12]
That plan's Deliveries A and B are complete. Its Step 11 (generation lifecycle) is built but unwired — that is
P12 here. Its Deliveries C/D (Steps 12–19: real Ferguson-corrected schema, checkpoint stores, run manifests)
map onto P9–P12 here. Do not run both plans concurrently; this document is authoritative from the branch point.

**D6. One Compose profile contract.** [affects P25]
`docker compose up` with no profile brings up infrastructure and bootstrap **only** — nothing that serves
traffic. The application tier is always an explicit opt-in.

| Profile | Services |
|---|---|
| *(default)* | `vault` `mongodb` `mongodb-rs-init` `neo4j` `valkey` `sqlserver` `sqlserver-init` `temporal-postgresql` `temporal` `runtime-configuration-init` |
| `containerized-app` | `backend` `return-workflow-worker` `return-orchestrator` `outbox-publisher` `frontend` |
| `dev-tools` | `temporal-ui` `seed-runner` `diagnostics` |

`scripts/start.{sh,ps1}` pass `--profile containerized-app` explicitly and accept `--dev` to add `dev-tools`.
No service depends on being in the default set to start.

**D7. Static gates during implementation; the full test campaign after functionality and cleanup.**
[affects all] Confirmed by the user after review. Implementation phases run compile, lint, typecheck,
configuration validation, affected contract generation, and a focused smoke or unit check **only where one is
needed to protect the change being made**. The full backend, frontend, integration, live-infrastructure, E2E,
resilience, and bootstrap/restart campaign runs once, after all functionality and cleanup are complete. See
§5. Two disciplines carry through every phase regardless, because they serve the same goal of not
accumulating broken code: tests for deleted code are deleted in the same commit, and architecture tests are
added in the phase that creates the module they guard.

**D8. uv is the single package manager; `uv.lock` is the single lockfile.** [affects P4, P25, P27]
Three resolution paths exist today and none share a lock: `backend/Dockerfile:13` runs `pip wheel .` against
`pyproject.toml` with **no lock at all**, the bash host scripts use `poetry install --sync` against
`poetry.lock`, and the PowerShell host scripts prefer `uv sync --frozen` against `uv.lock`. The container is
therefore not reproducible against either lock. `pyproject.toml` already uses PEP 621 `[project]` with
`==`-pinned dependencies, which uv consumes directly. `poetry.lock` and `[tool.poetry]` are deleted only after
Dockerfile, host scripts, and CI all build green from `uv.lock`. Full detail in design §5.2a.

**D9. Configuration activation propagates through a reconciler, or refuses and says so.** [affects P2, P15]
A release going ACTIVE is not the same as a running replica adopting it. Modules implement the epoch-keyed
two-phase protocol (`prepare_reconfigure` / `commit_reconfigure` / `abort_reconfigure` / `release_epoch`),
and adoption becomes visible through **one** replica epoch-pointer swap so no request can observe two
releases at once. A replica that cannot hot-reconfigure records a pending release, reports `/health/ready` as
degraded, and the UI names which replicas are behind. Running workflows stay pinned to their start release,
structurally: they hold a `RuntimeConfigurationView`, which is the only object able to read configuration
values. Full protocol in design §13.2.

**D11. LangGraph is the internal durable reasoning runtime for two components only.** [affects P5A, P7, P9–P11]
Order Discovery's reasoning and the Graph Schema Analyzer's reasoning engine / Analyzer Copilot. LangGraph is
an implementation detail behind existing contracts — it does **not** replace Temporal, the Return Session
Orchestrator, the Agent Registry, the AI Gateway, the Module Registry, the Graph Generation Lifecycle, the
System Store, or the Configuration Control Plane. Consequences:

- **The business-agent count stays at six.** The Analyzer remains an independent module with a reasoning
  engine, not a seventh workflow agent; the orchestrator continues to know nothing about it.
- **All AI calls still go through the AI Gateway** via `AgentAiPort` / `SchemaReasoningPort`. A LangGraph node
  may never construct a chat model or provider client — that would create a second routing path bypassing
  failover, rate limits, circuit breakers, interception, replay, safety, and metrics.
- **All tools wrap capability ports.** No node touches a Mongo client, SQL connection, Neo4j driver, AI
  provider, or another agent directly.
- **Production uses a persistent checkpointer** backed by SystemStore logical structures — never
  `InMemorySaver`, and never a library-chosen collection name.
- **Removing LangGraph from either component** must not require changing Temporal, the AI Gateway, any other
  agent, or any product screen.

Full contract in design §14. Budgets, thread IDs, idempotency keys, the two-suspension protocol, and the
seventeen architecture tests are specified there.

**D10. Session-to-generation binding defaults to `REBIND_ON_RESUME`.** [affects P12, P16]
A return session can sleep for days; ephemeral read leases cannot protect it. Default: the session records
its start generation for audit, holds no durable lease, and revalidates graph-derived facts on resume.
`PIN_STRICT` is available and takes a durable `GenerationSessionLease` — under which generation N is **never**
force-retired. Full contract in design §13.3.

---

## 4. Architectural rules

Every phase preserves these.

**4.1 Independent modules.** Business modules communicate through contracts, never implementation imports:
`Consumer → Port/Protocol → Module Registry → Configured Implementation`. Adding an implementation requires
minimal change outside the new module.

**4.2 Independent agents.** Agents never call other agents. `Agent A → typed result → Return Session
Orchestrator → workflow state → Agent B`. Each agent owns `agent_id`, `implementation_id`, `task_queue`,
`state_namespace`, input/output contracts, prompt ref, policy ref, AI route ref, capabilities, timeout, retry
policy, rate limit, max concurrency, circuit breaker, enabled status. Adding an agent must not modify existing
agent implementations.

**4.3 Configuration driven.** Never hardcode business table/collection/field names, source schemas, provider
or model names, graph labels or relationships, source mappings, system-collection physical names, or workflow
stage implementations.

**4.4 Source systems are read-only.** Configured external sources may be inspected, sampled, and queried. They
never receive `CREATE INDEX`, `ALTER TABLE`, `CREATE TABLE`, `UPDATE`, `DELETE`, `INSERT`, schema migrations,
or constraint changes. Graph-side changes are allowed. Platform-owned system structures are allowed.
*(Note: `infra/sqlserver/init/*.sql` is platform-owned local development infrastructure that stands in for a
source system — it is not covered by this rule.)*

**4.7 Reasoning runtimes are implementation details.** LangGraph is the durable reasoning runtime inside
Order Discovery and the Graph Schema Analyzer only. No graph object, state dict, or checkpointer crosses a
module boundary, and no consumer's type signature mentions it. It never becomes a second orchestrator, a
second AI routing path, or a way to reach a datastore. Design §14 is normative.

**4.6 Distributed correctness invariants are normative.** Design §13 specifies eight invariants —
capability-registry decoupling, configuration adoption, session/generation binding, generation handles,
interception resume atomicity, sensitive-data classification, fenced bootstrap leases, and atomic release
activation. Each names the mechanism and the test that enforces it. A phase that builds one of these
mechanisms builds its named test in the same commit; that test is the mechanism's specification, not a
regression net.

**4.5 Documentation is code.** Every changed production file gets accurate module documentation; every named
production class/function/method gets meaningful documentation; every independent module keeps a current
README; affected READMEs update in the same commit as the code. Anonymous callbacks and generated code are
exempt. There is no separate documentation-evidence phase.

---

## 5. Gate policy

**This section is normative and overrides any phase text that appears to contradict it.** Two regimes:
implementation phases stay structurally healthy without becoming validation projects; the behavioral campaign
runs once, after functionality and cleanup are complete.

| Phase | Permitted |
|---|---|
| 0 | one **optional** baseline suite run, as a reference for Phase 30 — not a gate |
| 1–29 | static checks, architecture checks, and **only** the focused invariant tests the phase explicitly names |
| 30 | the first complete behavioral campaign |

No phase between 1 and 29 runs a full backend, frontend, integration, or E2E suite. If a phase's text seems to
ask for one, §5 wins and the phase text is a defect — report it rather than running the suite.

### 5.1 Implementation gate (Phases 0–29)

Every phase ends with all applicable checks green before commit.

**Backend — always**
```bash
cd backend && uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run python -m compileall -q src
```

**Import validation — always** (catches the dangling-import class of breakage that lint misses)
```bash
cd backend && uv run python -c "import return_platform.main"
```

**Frontend** — any phase touching `frontend/`
```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

**Contracts** — any phase changing a router, request model, or response model
```bash
cd backend && uv run python scripts/export_openapi.py
cd .. && uv run python scripts/check_openapi_drift.py
```
Regenerated `openapi.json`, `backend/openapi/`, `frontend/openapi/`, and affected
`frontend/src/contracts/*.ts` are committed **in the same commit** as the code change.

**Configuration** — any phase touching `backend/config/`: every YAML parses; every canonical schema validates;
every referenced module ID, agent ID, workflow handler, AI task/route, and system structure resolves.

**Infrastructure** — any phase touching `compose.yaml`, `infra/`, or `scripts/`
```bash
docker compose config >/dev/null
bash -n scripts/*.sh
```
plus a PowerShell parse pass over `scripts/*.ps1`.

**Focused check — only where one is needed to protect the change.** Not a suite run. Add or run a narrow
unit/smoke check when the phase builds a correctness mechanism whose failure would be invisible to static
analysis and expensive to discover later. Specifically: the §13 invariant tests (fenced leases, atomic
activation, resume atomicity, generation handle threading, drain gating) are written **in the phase that
builds the mechanism**, because they are the specification of that mechanism, not a regression net. Nothing
else warrants a test run during implementation.

**Two disciplines that apply to every phase** — both serve "don't accumulate broken code", not validation:

1. **Test deletion.** When a phase deletes production code, the tests covering it are deleted or migrated in
   the same commit. The suite is never left referencing removed modules.
2. **Architecture tests.** The eleven checks in design §11 are added in the phase that creates the module
   they guard. They are static import/AST scans, not behavioral tests, and run inside the lint step.

### 5.2 Behavioral campaign (Phase 30)

Runs once, after all functionality and cleanup are complete:

```
full backend suite          full frontend suite         integration suite
live-infrastructure runs    end-to-end scenarios        resilience / restart
clean bootstrap             existing-state bootstrap
```

Phase 30 is scoped in full at §34. Budget real time for it — this is where the accumulated behavioral debt of
29 phases is discovered and paid.

**Live-stack rule.** Phases touching source field-path resolution, cursor semantics, graph projection, sync,
or generation lifecycle carry an explicit live-stack verification step in the phase body. This is not a test
suite — it is a manual run against the seeded Docker stack, and it stays in the implementation regime because
this repo has a documented history of in-memory doubles hiding physical-vs-logical name bugs that only real
infrastructure catches. Phases with this requirement: 3, 4, 7, 8, 10, 12.

---

## 6. Target architecture

```
backend/src/return_platform/
├── bootstrap/
├── platform/
│   ├── modules/          system_store/    auth/
│   ├── audit/            secrets/         outbox/
│   └── observability/
├── configuration/
│   ├── domain/  application/  sources/  integrations/  persistence/  api/
├── agents/
│   ├── contracts/  registry/
│   ├── order_discovery/  (+ reasoning/)   order_analysis/  return_workflow/
│   └── return_fulfillment/  bay_assignment/  feedback_learning/
├── business/
│   ├── orchestrator/  returns/  support/  fulfillment/  warehouse/  api/
├── graph/
│   ├── schema/  query/  connectors/  projection/  sync/  lifecycle/
├── graph_schema_analyzer/
│   ├── domain/  application/  reasoning/  ports/  persistence/  api/
├── ai/
│   ├── gateway/  providers/  routing/  safety/  interception/  metrics/  api/
└── main.py
```

**No module has an `adapters/` package.** Cross-module binding lives in `bootstrap/adapters/` — the only
package permitted to import two modules (design R2, §13.1). An `adapters/` directory inside
`graph_schema_analyzer/`, `agents/`, or any other module would recreate exactly the compile-time coupling the
capability registry exists to eliminate.

**The complete structural specification is `UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md`** — every directory and
file with its responsibility, the core contracts with real signatures, the full system-store data model, the
complete API surface, every state machine, the six machine-checkable architecture rules, and a file-by-file
migration map from the current tree. This plan says *when*; that document says *what*. Each phase below
references the sections of it that it realizes.

Do not perform blind file moves to reach this tree. Introduce contracts and move ownership first; move or
delete files only when the dependency graph is ready.

---

## 7. Branch setup

```bash
git fetch --prune origin
git status --short
git rev-parse origin/feat/v2-order-discovery-integration
git switch -c refactor/unified-return-platform origin/feat/v2-order-discovery-integration
git push -u origin refactor/unified-return-platform
```

Do not reset, clean, delete, or overwrite unrelated local work. If the source branch has advanced past
`c3cdd35` when work starts, branch from the fetched remote HEAD, record that SHA in the root README migration
section, re-verify §2, and continue. Create no additional implementation branches unless a real recovery
situation requires one.

---

# Track 0 — Baseline

## Phase 0 — Consolidation baseline

**Objective.** Create the branch and record exactly what is being transformed.

**Work.**
1. Create the branch per §7.
2. Capture implementation inventory into `docs/consolidation/baseline-inventory.md` — registered routers with
   their prefixes, frontend routes, agents, Temporal workflows, workers, source connectors, graph connectors,
   AI providers, system-store structures, configuration domains, Compose services, bootstrap scripts. Facts
   only; no assessment, no proof reports.
3. Add a temporary migration section to `README.md`: consolidation source branch, source commit, consolidation
   branch, target application architecture.
4. Optionally record a baseline test result (`uv run pytest -q`, `npx vitest run`) in the inventory file.
   **This is the only full suite run permitted before Phase 30** (D7) — it exists as the reference the Phase 30
   campaign is compared against, not as a gate to re-run. Skip it if the suite is already known-green at the
   baseline commit.

**Gate.** `git status`, `git diff --check`, inventory recorded.

**Commit.** `chore: establish unified platform consolidation baseline`

---

# Track 1 — Platform foundations

*Additive only. Nothing is removed in this track; the existing boot path keeps working alongside the new one.*

## Phase 1 — Platform kernel

**Objective.** Create the module extension model before migrating any domain onto it.

**Current state.** No module kernel exists. `V2PlatformServices` (`v2/services.py:1092`) is the closest thing —
an aggregate that hardcodes its own children.

> **Execution note — split into 1A and 1B; architecturally one phase.**
> Phase 1 establishes the contracts every later module implements, so an error here propagates across almost
> every downstream phase. Split the work into two commits to shrink the review and rollback surface: if 1A
> exposes a contract problem, it is corrected before the much more consequential epoch and reconfiguration
> layer is built on top of it.
>
> **1A and 1B are not independently releasable alternatives.** 1B must immediately follow 1A, before Phase 2
> begins — the final `ModuleRuntime` contract and the epoch model are foundational to configuration adoption,
> and Phase 2 builds directly on both.
>
> ```
> Phase 1A   implement → docs → static/architecture checks → commit → push
> Phase 1B   implement → docs → epoch/concurrency checks   → commit → push
> ONLY THEN  Phase 2
> ```

---

### Phase 1A — Neutral contracts and capability kernel

**Create.**
```
platform/contracts/     runtime_configuration.py  consistency.py  epoch.py
                        clock.py  correlation.py  README.md
platform/capabilities/  contracts.py  registry.py  errors.py  README.md
platform/modules/       descriptor.py  registry.py  builtins.py  exceptions.py  README.md
bootstrap/adapters/     README.md   (populated as modules land)
```

**Neutral platform contracts come first (design §7.1).** `platform/*` must not name a type owned by any domain
module. A context field typed `configuration.ConfigurationHandle` or `graph.lifecycle.GenerationHandle` would
violate the very rule the capability registry exists to enforce — and because the kernel is built here, that
mistake would be baked into every module built after it. Declare neutral protocols that domain types
structurally satisfy:

| Platform protocol | Satisfied later by |
|---|---|
| `RuntimeConfigurationView` | `configuration.domain.handle.ConfigurationView` |
| `RuntimeConfigurationHandle` | `configuration.domain.handle.ConfigurationHandle` |
| `ConsistencyHandle`, `ConsistencyChanged` | `graph.lifecycle.handles.GenerationHandle`, `GenerationChanged` |
| `RuntimeEpoch` | `bootstrap` epoch allocator (consumed in 1B) |

**The view/handle split makes configuration pinning structural.** `RuntimeConfigurationView` is immutable,
names one release, and is **the only object with `section()`**. `RuntimeConfigurationHandle` resolves views
(`current(epoch)`, `await pinned(release_id)`) and has no read method of its own. Without this split a session pinned to
release 41 could call `configuration.section("return_policy")` and silently execute release 42's rules while
still reporting itself pinned to 41. Modules get a handle (they must observe adoption); agents and request
boundaries get a view.

`ConsistencyHandle` deliberately says nothing about graph generations — an opaque `token`, `assert_current()`,
and `release()`.

**Descriptors and registry.**
`ModuleFactory` protocol: `descriptor` property, `create(context, config) -> ModuleRuntime`.
`ModuleDescriptor`: `module_id`, `module_kind`, `implementation_id`, `version`, `capabilities`,
`configuration_schema`, `required_platform_capabilities`.
Registry responsibilities: register allowlisted implementation, reject duplicate implementation IDs, resolve
implementation, validate capabilities, construct configured module, report health.

**Capability registry — the decoupling mechanism (design §13.1, §7.3).** `ModuleRuntimeContext` carries
platform services plus `CapabilityRegistry` and **no named field for any module's service** — no `.ai`, no
`.knowledge`, no `.graph`. A consumer declares the Protocol it needs in its own `ports/` and resolves it;
structural typing means neither side imports the other.

**Registrations are keyed by `(capability, contract)`, not by capability alone.** This is not a refinement —
keying on the name alone is a correctness bug. `AI_INVOCATION` must serve both `AgentAiPort.invoke(...)` and
`SchemaReasoningPort.reason(...)`; with a single key the second publication hits `DuplicateCapability`, and
whichever consumer loses gets a structural mismatch at resolve time. The pair lets one AI Gateway back many
consumer shapes:

```python
publish(AI_INVOCATION, AiGatewayContract,   "ai",        gateway)          # ai/module.py
publish(AI_INVOCATION, AgentAiPort,         "bootstrap", AgentAiAdapter(gateway))
publish(AI_INVOCATION, SchemaReasoningPort, "bootstrap", AnalyzerAiAdapter(gateway))
resolve(AI_INVOCATION, SchemaReasoningPort) # analyzer gets its own shape
```
Both adapters wrap the same gateway, so there is still exactly one AI execution path.

**Conformance needs three layers — do not rely on `isinstance` alone.** `@runtime_checkable` +
`isinstance()` proves only that the named methods *exist*; it does not check parameter names, arity, types, or
returns. An adapter with `reason(self, prompt)` against a port declaring `reason(self, task_id, context)`
passes publication and fails at first call.

| Layer | Catches |
|---|---|
| publication `isinstance` | missing/misnamed methods, wrong object |
| mypy, in the phase gate | wrong parameters, arity, types, returns |
| per-adapter contract test | wrong behaviour with a correct signature |

Make the static layer real by giving every adapter a typed factory — the return annotation is what mypy
checks: `def build_analyzer_ai_adapter(gateway: AiGatewayContract) -> SchemaReasoningPort: ...`. A bare
`publish(..., AnalyzerAiAdapter(gateway))` with no typed factory is a review defect.

**Security constraint.** The registry must not accept arbitrary Python import paths from YAML. Use explicitly
registered built-ins and/or controlled package entry points only.

**Docs.** `platform/contracts/README.md`, `platform/capabilities/README.md`, `platform/modules/README.md`,
root README architecture section.

**Gate.** Implementation gate, backend only. Architecture tests built here and kept permanently:
`tests/platform/test_layering.py` (no `platform.*` module imports `configuration`, `graph`, `agents`,
`business`, `ai`, or `graph_schema_analyzer`), `tests/platform/test_no_module_cross_imports.py`,
`tests/platform/test_capability_keying.py`. Focused check: `tests/platform/test_module_registry.py`.

**Commit.** `refactor(platform): add neutral contracts and capability kernel`

---

### Phase 1B — Epoch-aware module lifecycle

**Create.**
```
platform/modules/   contracts.py  lifecycle.py      ModuleRuntime, ModuleRuntimeContext,
                                                    ReconfigureOutcome, ordered lifecycle
bootstrap/          context.py  lifespan.py  activation.py  capabilities.py
                    epoch.py    health.py    errors.py
```

**`ModuleRuntime` protocol.** `initialize()`, `publish_capabilities(registry)`, `resolve_capabilities()`,
`prepare_reconfigure(epoch)`, `commit_reconfigure(epoch)`, `abort_reconfigure(epoch)`,
`release_epoch(epoch)`, `health()`, `shutdown()`, `router` property.

**Four ordered passes**, built now and not retrofitted:
```
create()  →  publish_capabilities()  →  bootstrap/adapters publish  →  resolve_capabilities()
```
A module is constructed before any cross-module capability exists and binds its ports only after every
publication — including the bootstrap-constructed adapters. Construction order becomes independent of
dependency order, which is what prevents circular construction as modules are added. `initialize()` runs after
resolution, so a module may use its resolved ports during startup.

**Epoch-keyed two-phase reconfiguration (design §13.2).** `ModuleRuntime` declares
`prepare_reconfigure(epoch)` / `commit_reconfigure(epoch)` / `abort_reconfigure(epoch)` /
`release_epoch(epoch)`, never a single `reconfigure`. Two distinct problems, and either one alone still leaves
a mixed runtime:

- **One-phase cannot be all-or-nothing.** If module A rebuilds its pools and module B then returns
  `RESTART_REQUIRED`, the replica is already running a mix.
- **Per-module commits are not replica-atomic.** Even with every commit an atomic non-failing swap, they
  happen in sequence — a request admitted between the first and last sees A on X and B on X-1.

So `commit_reconfigure` makes a candidate *addressable* under an epoch; it does **not** make it current. One
replica-scoped epoch-pointer swap does that, in a single write. A request captures its epoch once at the outer
boundary and every module resolution during that request uses it, so a request observes exactly one release
across every module for its whole life. Old-epoch resources are released only after in-flight requests drain —
the same drain-before-release discipline as graph generations.

All fallible work belongs in `prepare`; `commit`, `abort`, and `release_epoch` are non-failing by
construction. If a `commit` raises anyway, the replica marks itself `UNAVAILABLE` and requires restart rather
than serving a partial promotion.

**Modules resolve resources by epoch**, from an epoch-keyed map — never from a mutable `self._current`. This
is the constraint that makes per-request epoch capture meaningful, and it must be established here so every
later module is written that way from the start.

**Post-review amendment.** External review of the first cut of this phase found four P0 correctness defects
and two P1 completion gaps before Phase 2 could safely build on it. All six are fixed in this phase, not
deferred:

1. **Epoch capture and lease acquisition are one atomic operation, not two.** The first cut exposed
   `EpochPointer.current` and `EpochLeaseTracker.acquire()` as independent calls on independent objects — a
   request could read the current epoch, and before it registered as a holder, a concurrent reconfiguration
   could swap past it and release that epoch's resources out from under it. Replaced both with a single
   `EpochAdmission` component holding the pointer, an explicit `CURRENT → DRAINING → RELEASED` state machine,
   and the holder counts, all behind one lock, with `acquire_current()` as the only admission entry point —
   there is no way to even express "read current" separately from "become a holder of it." The lock is a real
   `threading.Lock`, not `asyncio.Lock`, because the original also had unsynchronized dict read-modify-write
   that could lose updates under genuine OS-thread concurrency (e.g. sync route handlers in a thread pool).
2. **Abort on refusal or failure now covers every module, not just the ones that already prepared.** The
   design always specified "abort_reconfigure(X) on every module" (§13.2) — the first cut's code only aborted
   modules that appeared *before* the refusing/failing one, on the mistaken assumption that a module which
   never returns `READY` has nothing to clean up. A module can allocate real candidate resources before
   deciding to refuse, or before failing outright; `_abort_all` now calls every module unconditionally,
   best-effort, collecting any abort failures into one `ExceptionGroup` rather than stopping at the first.
3. **A `commit_reconfigure` failure now makes the replica fail closed.** The first cut let a raised commit
   propagate as an ordinary exception a caller could catch and ignore. `ReconfigurationCoordinator` now tracks
   `ReplicaStatus` (`AVAILABLE`/`DEGRADED`/`UNAVAILABLE`); a commit failure raises `FatalReconfigurationError`,
   flips the status to `UNAVAILABLE`, and every subsequent `acquire_current()` or `reconfigure()` call is
   refused from then on — matching "the replica marks itself UNAVAILABLE, stops serving, and requires restart"
   exactly, with no path back to `AVAILABLE` short of a process restart.
4. **Module initialization is dependency-ordered, not caller-order.** `ModuleDescriptor` gained
   `initialization_dependencies: frozenset[str] = frozenset()` (stable module IDs, not Python imports), and
   `platform/modules/lifecycle.py::topological_order()` does DFS-based ordering with cycle detection (a
   self-dependency is a cycle of length one, caught the same way) and missing-dependency detection.
   `bootstrap/activation.py::activate()` now computes this order before `construct_all`, so the resulting
   module list — and therefore `initialize_all`'s order and `shutdown_all`'s reverse order — is correct by
   construction. This was flagged as a design amendment, not just a code fix: `ModuleDescriptor` did not carry
   dependency information before this.
5. **A failed `initialize()` now unwinds what already started.** `initialize_all` previously propagated
   immediately on a raise, leaking every module that had already initialized (never shut down). It now tracks
   successfully-initialized modules and shuts them down in reverse order before re-raising; if cleanup itself
   also fails, both the original failure and the cleanup failures are reported together via `ExceptionGroup`.
6. **The kernel is now actually attached alongside `main.py`'s existing boot process**, not just built next to
   it unreferenced. `main.py`'s existing `lifespan()` now constructs a `ModuleRegistry` with zero registered
   modules, runs it through `module_lifespan()` (construct → publish → resolve → initialize → yield →
   shutdown), and wraps the pre-existing `yield` with it — proving the full mechanism executes end-to-end with
   zero effect on existing behavior. The first real module replaces the empty registry starting in a later
   phase.

A latent instance of the read-only-Protocol-property defect from Phase 1A (design §7.1) was also found and
fixed here: `ModuleRuntimeContext` itself declared its fields as plain attributes, which a frozen dataclass
implementation (`bootstrap/context.py::RuntimeContext`) cannot satisfy under mypy strict. Converted to
`@property`, matching the fix already applied to the four Phase 1A contracts.

**`main.py`.** The kernel is wired in as described in amendment 6 above — additive, zero business modules,
proven to execute correctly, with the pre-existing boot logic completely untouched around it.

**Docs.** `platform/modules/README.md` (lifecycle section), `bootstrap/README.md`, root README architecture
section — all updated to describe `EpochAdmission`, dependency-ordered initialization, and the `main.py`
wiring rather than the superseded `EpochPointer`/`EpochLeaseTracker` split.

**Gate.** Implementation gate, backend only. Focused checks (mechanism specifications, per §5.1), all in
`tests/platform/`: `test_epoch_admission.py` (atomicity of capture+lease, the CURRENT/DRAINING/RELEASED state
machine, idempotent release, and a real multi-threaded stress test proving no lost holder-count updates);
`test_epoch_drain_before_release.py`; `test_prepare_abort_leaves_live_untouched.py` (both the
`RESTART_REQUIRED` and the prepare-exception path, both aborting every module); `test_commit_failure.py`
(replica goes `UNAVAILABLE`, the pointer never swaps, and no further admission or reconfiguration succeeds);
`test_initialization_ordering.py` (dependency order, cycle detection, self-dependency, missing dependency);
`test_initialization_cleanup.py` (a failed `initialize()` unwinds prior modules, and cleanup failures are
reported alongside the original).

**Commit.** `refactor(platform): add epoch-aware module lifecycle` (original), plus
`fix(platform): close epoch admission race, full-abort, fatal-commit, and dependency-ordering gaps` (this
amendment).

**Second post-review amendment.** A follow-up review of the first amendment found two further P0 concurrency
races and two P1 robustness gaps in the same mechanism, all closed here:

1. **`reconfigure()` was not serialized.** `EpochAdmission` itself was correctly lock-protected, but nothing
   stopped two concurrent `reconfigure()` calls (targeting different epochs) from running their multi-`await`
   prepare/commit phases concurrently — an older attempt's commit finishing after a newer one's swap could
   regress the current epoch. `ReconfigurationCoordinator` now holds an `asyncio.Lock` around the entire
   `reconfigure()` body, so two attempts on the same coordinator never interleave. As defense in depth,
   `EpochAdmission.begin_swap()` also gained an `expected_current_epoch` parameter: it raises
   `StaleReconfiguration` if the current epoch has moved since the caller last observed it, or if the target
   epoch is not strictly newer than current — both checked inside the same lock as the swap itself, so this
   protects the invariant even if a future caller reaches `begin_swap` directly.
2. **`UNAVAILABLE` and request admission were still two synchronization domains.** The prior fix correctly
   made a commit failure set `ReplicaStatus.UNAVAILABLE`, but `ReconfigurationCoordinator.acquire_current()`
   checked that status *before* calling into `EpochAdmission`'s separately-locked `acquire_current()` — a
   request could observe `AVAILABLE`, then a concurrent fatal commit could flip to `UNAVAILABLE`, then the
   request could still be admitted. The accepting/closed flag now lives inside `EpochAdmission` itself,
   checked under the same lock as holder registration; `ReconfigurationCoordinator` no longer keeps its own
   separate status field, deriving `status` from `EpochAdmission.is_accepting()` instead, so there is only one
   source of truth to go stale.
3. **`RELEASED` was recorded before module cleanup actually succeeded.** The prior `try_release()` transitioned
   `DRAINING → RELEASED` and only then ran module cleanup — a cleanup failure after that transition left the
   epoch permanently marked done with some modules never actually released, and no way to retry through this
   path. Added an intermediate `RELEASING` state: `begin_release()` transitions into it (or confirms it's
   already there, letting a retry proceed), and `finish_release()` only moves to `RELEASED` once every
   module's `release_epoch` has actually completed without raising. A failed retry leaves the epoch in
   `RELEASING`, re-invoking every module's cleanup on the next attempt — safe because `release_epoch` is
   already documented as must-not-fail / idempotent, the same contract `abort_reconfigure` relies on.
4. **The module whose own `initialize()` failed was never shut down.** `initialize_all` correctly unwound
   modules that succeeded *before* the failure, but excluded the failing module itself — even though it may
   have partially-initialized resources (a connection pool opened before a later validation step raised).
   `ModuleRuntime.shutdown()`'s contract now explicitly requires tolerating this; `initialize_all` calls
   `shutdown()` on the failing module first, then the previously-initialized ones in reverse.

**Docs.** Design doc §13.2 gained the `RELEASING` state in its lifecycle diagram and four amendment paragraphs
covering serialization, admission-closed atomicity, and release finalization; `ModuleRuntime.shutdown()`'s
docstring (both design doc and code) documents the partial-initialization tolerance requirement.
`bootstrap/README.md` updated to describe the reconfigure lock, `begin_swap` fencing, and the
`RELEASING`/`RELEASED` split.

**Gate.** Implementation gate, backend only. Focused checks added: `test_concurrent_reconfigure.py`
(`test_concurrent_reconfigurations_are_serialized` — proves no interleaving under real concurrent
`reconfigure()` calls); `test_epoch_admission.py` gained
`test_begin_swap_requires_expected_current_epoch` and `test_older_reconfiguration_cannot_replace_newer_epoch`;
`test_commit_failure.py` gained `test_fatal_commit_atomically_closes_request_admission` (a barrier-synchronized
multi-threaded stress test, not just the sequential case); `test_epoch_drain_before_release.py` gained
`test_release_failure_does_not_finalize_epoch` and `test_release_cleanup_can_be_retried`;
`test_initialization_cleanup.py` gained `test_failing_initializer_is_also_shut_down` and the existing
prior-modules test was corrected to expect the failing module's own `shutdown()` call too.

**Commit.** `fix(platform): close concurrent reconfiguration and epoch cleanup races` (this amendment).

**Third post-review amendment.** A further review of the second amendment found one more P0 in the same
mechanism, one confirmed-stale documentation claim, and one test-collection claim that did not reproduce
against the actual tree:

1. **`EpochAdmission.release()` tracked a bare integer count, not individual leases, so it was only
   accidentally idempotent.** `release()` decremented `holders` on every call; releasing the *same* acquired
   lease twice decremented twice, indistinguishable from two different holders each releasing once. A caller
   bug or a retried release path could therefore make an epoch appear fully drained while a genuinely
   different holder was still active — undermining the exact invariant this mechanism exists to guarantee.
   `acquire_current()` now returns an `EpochLease` (a unique `lease_id` plus the acquired epoch's `epoch`/
   `release_id`, so it structurally satisfies `RuntimeEpoch` and drops in anywhere a plain epoch value was
   expected). `EpochAdmission` tracks a `set[str]` of active lease IDs per epoch instead of a count;
   `release()` now takes the lease and discards its ID from that set — an operation that is unconditionally
   idempotent, because removing an absent element from a set is a no-op by definition, unlike decrementing a
   number past zero.
2. **The root README still named the superseded `EpochPointer`/`EpochLeaseTracker` split and claimed the
   kernel was "not wired into the application's actual startup yet."** Both were stale relative to the first
   and second amendments above — the split was replaced by `EpochAdmission` two amendments ago, and `main.py`
   has wired the zero-module kernel into its real `lifespan()` since this phase's original commit. Corrected.
3. **A claimed stale test import did not reproduce.** A report described `tests/platform/test_epoch_drain_before_release.py`
   as still importing the superseded `EpochPointer`/`EpochLeaseTracker` names and therefore failing at
   collection. Checked against the actual tree: that file was already migrated to `EpochAdmission` in the
   second amendment above, and a full-repository search found no source file referencing either superseded
   name — the only hit was a stale `.pyc` bytecode cache for the test module deleted in the *first* amendment
   (`test_epoch_visibility.py` → `test_epoch_admission.py`), which is gitignored and not a code defect.
   Removed the stray cache file for hygiene; no source or test change was needed for this item.

**Docs.** Design doc §13.2 gained a new amendment paragraph on lease-identity tracking and the extended
"Enforced by" list; `bootstrap/README.md` updated throughout from "holder counts" to "active leases by unique
identity"; root `README.md` corrected on both the class names and the startup-wiring claim.

**Gate.** Implementation gate, backend only. `test_epoch_admission.py`'s
`test_epoch_release_is_idempotent`/`test_concurrent_lease_counts_are_correct` were replaced with three tests
that exercise genuine per-lease identity rather than a single shared epoch value:
`test_duplicate_release_does_not_decrement_another_holder` (the exact two-holder scenario the defect allows),
`test_epoch_cannot_release_while_any_unique_lease_remains`, and `test_concurrent_lease_release_is_idempotent`
(many threads redundantly releasing the same lease set, proving the epoch drains exactly once, never early
and never stuck).

**Commit.** `fix(platform): track epoch holders by unique lease identity, not a bare count` (this amendment).

---

## Phase 2 — Canonical configuration contracts

**Objective.** One configuration model replacing the V1/V2/runtime-config fragmentation.

**Correction to the draft plan.** This is not greenfield. `backend/config/v2/manifest.yaml` already declares 19
modules across agents, policies, workflows, sync, sources, mappings, graph, and platform. `main.py:369` loads
them. They land as `DRAFT` and nothing activates them (`v2/services.py:157`). The work is (a) lifting that
content out of the `v2` namespace and (b) writing loaders that actually consume it.

**Canonical domains.** `platform`, `system_store`, `modules`, `agents`, `workflow`, `sources`, `integrations`,
`graph`, `ai`, `features`.

**Work.**
1. Typed configuration contracts under `configuration/domain/` for each canonical domain.
2. Move `backend/config/v2/*` → `backend/config/*`, dropping the version namespace. Preserve the manifest
   mechanism; keep module IDs stable (`agent.order_discovery`, `platform.system_store`, …).
3. Replace `ModuleStatus.DRAFT` hardcoding with real status handling and a release lifecycle:
   `DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED`.
4. Configuration precedence: `bootstrap environment → version-controlled baseline → validated active release →
   immutable runtime snapshot`. Only deployment/bootstrap parameters belong in `.env`; secrets stay in Vault.
5. **Atomic activation (design §13.8).** "Exactly one ACTIVE release" must be enforced, not asserted. Build
   `configuration/application/activation.py`: a Mongo transaction (the replica set already exists via
   `mongodb-rs-init`) that supersedes the current ACTIVE, activates the target, and CASes the
   `configuration_active_pointer` singleton — all three or none. Add a partial unique index on
   `configuration_releases where status = "ACTIVE"` as defence in depth. The loser of a concurrent activation
   gets `409` and changes nothing.
   Test built here: `tests/configuration/test_concurrent_activation.py`.
6. **Runtime adoption (design §13.2).** A release being ACTIVE is not a running replica using it. Build:
   - `ConfigurationView` implementing `RuntimeConfigurationView` — immutable, names one release, and is the
     **only** object with `section()`. `ConfigurationHandle` implementing `RuntimeConfigurationHandle`
     (`current(epoch)` / `async pinned(release_id)`) resolves views and has no read method of its own. A pinned session
     resolves `await handle.pinned(session.configuration_release_id)` once at its boundary; because the view is the
     only readable object, an agent physically cannot reach a newer release.
   - The **epoch-keyed two-phase** protocol declared in Phase 1B: `prepare_reconfigure(epoch)` on every
     module → all `READY`/`NO_CHANGE` → `commit_reconfigure(epoch)` on every module → **one** replica
     epoch-pointer swap; any `RESTART_REQUIRED` or raise → `abort_reconfigure(epoch)` on every module,
     **including those that already prepared**. The single pointer swap is what makes adoption visible
     atomically: per-module commits alone let a request admitted mid-sequence see module A on X and B on X-1.
     Requests capture their epoch at admission; drained epochs are released afterwards.
   - `bootstrap/reconciler.py` watching the active pointer and driving that protocol. On abort the replica
     keeps its adopted snapshot entirely, records `pending_release_id`, and `/health/ready` reports degraded.
     If a `commit` nevertheless raises, the replica marks itself `UNAVAILABLE` and requires restart rather
     than serving a partial swap.
   - `configuration_adoption` — one TTL-heartbeated document per instance, so the UI can say "ACTIVE, adopted
     by 2 of 3 replicas" instead of just "ACTIVE".
   - Running workflows stay pinned: sessions resolve via `handle.pinned(release_id)` for their whole life, and
     pinned releases are retained until no open session references them.
   Tests built here: `tests/configuration/test_reconfiguration_protocol.py`;
   `tests/configuration/test_late_restart_required_aborts_all.py` — the **last** module polled refuses after
   every earlier module prepared, asserting all received `abort_reconfigure` and nothing was promoted;
   `tests/configuration/test_requests_never_observe_mixed_release_during_adoption.py` — continuous concurrent
   requests during a release change, asserting each reports exactly one release ID across all modules;
   `tests/agents/test_agent_reads_pinned_configuration_after_new_release_activation.py` and
   `tests/agents/test_running_workflow_never_reads_current_release.py`.
7. Compatibility adapters translating `config/returns/production.yaml`, `config/ai_gateway.yaml`,
   `config/dynamic_knowledge/*`, and the existing runtime configuration into canonical contracts. **Delete no
   old configuration in this phase.**

**Docs.** `configuration/README.md`, `backend/config/README.md` documenting every key; root README
configuration architecture.

**Gate.** Implementation gate + configuration validation + duplicate-configuration-authority scan. **No
suite run** (§5). Focused checks built here: `tests/configuration/test_concurrent_activation.py`,
`tests/configuration/test_reconfiguration_protocol.py`,
`tests/configuration/test_late_restart_required_aborts_all.py`.

**Commit.** `refactor(config): introduce canonical runtime configuration model`

**Post-review amendment.** An external review against the actual Phase 2 code raised eight findings (six P0,
two P1): fail-open compatibility translation, missing module dependency validation, an AI empty-map
validation bug, `dynamic_knowledge` directory globbing bypassing the manifest, unverified pinned-release
checksums, weak validate/approve CAS tests, missing manifest schema-version enforcement, and an
insufficiently rigorous concurrent-activation test. Verifying each against the current tree found that seven
of the eight were already fixed by the preceding commit
(`fix(configuration): complete Phase 2 release lifecycle, semantic validation, and compatibility
translation`): `compatibility.py` now raises `ConfigurationValidationError` on any malformed AGENT / SOURCE /
GRAPH / MAPPING / SYNC / PLATFORM payload and never globs `dynamic_knowledge/*`; `validator.py` validates
dependency existence, self-dependency, and cycles (`_validate_module_dependencies`) and reverse completeness
per domain (`_validate_reverse_completeness`); the AI route check now gates on `task_id not in ai_tasks`
directly rather than `ai_tasks and task_id not in ai_tasks`, closing the empty-map bypass; `release_service.py`
recomputes and compares the snapshot checksum before every `DRAFT → VALIDATED` transition, and
`release_service.py::RuntimeConfigurationHandleImpl.pinned()` recomputes the checksum of every historical
snapshot before constructing its view, failing closed with `ConfigurationIntegrityError` on mismatch;
`loader.py` rejects any `schema_version` outside `SUPPORTED_MANIFEST_SCHEMA_VERSIONS = {"2.0"}`; and
`test_concurrent_validate_cas_only_one_wins` / `test_concurrent_approve_cas_only_one_wins` already use a
shared `asyncio.Barrier` and assert `success_count == 1, failure_count == 1` exactly.

The one finding that still had a real gap — the concurrent-activation test's fake session having no
transactional rollback semantics — led to two defects the review itself did not name, found while closing it:

1. **`activation.py` never actually ran.** `async with session.start_transaction():` is missing an `await`.
   `pymongo`'s async driver makes `start_transaction()` a coroutine that must be awaited to obtain the
   context manager; without it, every real call to `activate_release()` raised `TypeError` before touching
   the database. This type-checked fine against `test_release_lifecycle.py`'s hand-rolled session mock
   (whose `start_transaction()` returned a plain object, not a coroutine) and was invisible without a live
   MongoDB replica set to run against — which is exactly why the review's push for a real-transaction test
   mattered. `workflows/persistence.py` already had the correct `async with await session.start_transaction()`
   form; `activation.py` now matches it, and the mock's `start_transaction()` was made `async def` so it can
   no longer hide a missing `await` at the real call site.
2. **`test_concurrent_activation.py` targeted the wrong database and collection.** It read from
   `client.get_database("return_platform")` / collection `"configuration_activation_pointer"`, but
   `ActivationService` always operates against `get_database("platform")` / `"configuration_active_pointer"`
   (fixed names, independent of the business `mongo_database` setting). The test's assertions on the pointer
   document were therefore checking an always-empty collection — it could never have caught a real regression,
   in either direction. Corrected to the names `ActivationService` actually uses, verified by starting the
   project's own `mongodb` + `mongodb-rs-init` compose services and running the test against them directly
   (it fails with a clear `TypeError`/`ServerSelectionTimeoutError` before either fix, and passes after both,
   stable across 5 consecutive runs).

`test_concurrent_activation.py` was also rewritten to release both racing activations from a shared
`asyncio.Barrier` and to assert every invariant the review asked for: exactly one winner; the pointer's
`release_id` and `checksum` match that specific winner, not just "some" release; the pointer version advances
by exactly one; the loser is left with no partial mutation (`status` still `APPROVED`, no `activated_at`, no
`superseded_by`); and the previously-active release is superseded by the winner and never by the loser.

**Commit.** `fix(configuration): fix transaction await bug and target correct collection in activation test`
(this amendment).

---

## Phase 3 — Configuration-driven system store

**Objective.** Application-owned structures create themselves safely at startup.

**Correction to the draft plan.** `dynamic_knowledge/internal_store/` already implements most of this:
`InternalStoreAdapter` with Mongo, Neo4j, and SQL implementations, and `InternalStoreBootstrapper.bootstrap()`
already does inspect → `MISSING` create → `INCOMPATIBLE` raise → `COMPATIBLE` reuse → `ensure_indexes`. It is
the seed for this phase, not a competitor. The genuine gaps are **bootstrap locking**, **schema-version
history / forward migrations**, and **logical→physical name resolution**.

**Work.** Create `platform/system_store/` with `contracts.py`, `manifest.py`, `bootstrap.py`, `migrations.py`,
`locking.py`, `mongo.py`, `repository.py`, `README.md` — migrating the `internal_store` adapters and
bootstrapper rather than rewriting them.

Canonical provider: Platform MongoDB. `SystemStoreAdapter` remains a port so another provider can be added
without touching consumers. The existing Neo4j and SQL adapters are retained as proven implementations.

**Manifest** (`backend/config/platform/system_store.yaml`, promoted from `config/v2/platform/`). Extend the
existing keys (`provider`, `allowed_providers`, `auto_bootstrap_missing_structures`, `migration_mode`,
`fail_closed_on_drift`, `migration_lock_required`) with a `structures` block:

```yaml
system_store:
  provider: mongodb
  structures:
    conversations:         { physical_name: platform_conversations,        schema_version: 1,
                             indexes: [ { keys: { conversation_id: 1 }, unique: true } ] }
    ai_traces:             { physical_name: platform_ai_traces,            schema_version: 1 }
    ai_interceptions:      { physical_name: platform_ai_interceptions,     schema_version: 1 }
    graph_schema_drafts:   { physical_name: platform_graph_schema_drafts,  schema_version: 1 }
    graph_generations:     { physical_name: platform_graph_generations,    schema_version: 1 }
    audit:                 { physical_name: platform_audit,                schema_version: 1 }
```

**Logical naming is mandatory.** Repositories resolve `system_store.collection("ai_interceptions")`. No
`db["platform_ai_interceptions"]` anywhere in business code.

**Startup algorithm.**
```
acquire FENCED lease (token T) + start heartbeat
for each configured structure:
    inspect
    if missing: create; create required indexes        ← guarded on token T
    if present: validate; reuse
    apply pending forward migration                    ← guarded on token T
    record schema version                              ← guarded on token T
release lease (always, via finally)
```

**Fenced leasing is mandatory (design §13.7).** A TTL lock with an owner ID is not sufficient — a migration
slower than the TTL lets a second instance acquire the lock and run the same migration concurrently. Build:
- `FencedLease` = `lease_id` + `owner_instance_id` + `fencing_token` (monotonic `$inc` on a `fencing_tokens`
  document) + `acquired_at` + `heartbeat_at` + `expires_at`, with a **background heartbeat** renewing at a
  fraction of the TTL. **All of these are persisted on the lock record** — `bootstrap_locks` carries
  `lock_name`, `lease_id`, `owner_instance_id`, `fencing_token`, `acquired_at`, `heartbeat_at`, `expires_at`.
  A record holding only `lock_name`/`owner`/`expires_at` cannot fence: `owner` alone does not distinguish a
  reused process identity from the live holder.
- Heartbeat, release, and every protected write CAS on `(lock_name, lease_id, fencing_token)` — not on
  `owner`, and not on `lock_name` alone.
- **Abort on heartbeat failure.** If renewal fails — expired, or token superseded — the holder raises
  immediately and does not finish the migration it was running.
- **Every** migration write and version-ledger write is a conditional update guarded on
  `fencing_token == T`, so a paused-then-resumed stale holder is rejected at the store, not merely warned.
- Each migration is transaction-wrapped or independently idempotent, so a partial application is re-runnable.

Tests built here: `tests/platform/test_lease_heartbeat.py`,
`tests/platform/test_fenced_writes_reject_stale_token.py`, `tests/platform/test_migration_idempotence.py`.

**Encryption support (design §13.6).** Add `system_store/encryption.py` and `platform/secrets/envelope.py`
now, so structures declared `encrypted: true` with a `retention` in the manifest can exist from Phase 9
onward. The store layer must **refuse a plaintext write** to an `encrypted` structure.

Never drop a valid existing structure during normal startup. Drift handling is configuration-selected `FAIL` or
`WARN`. No destructive auto-repair.

**Docs.** `platform/system_store/README.md` covering logical resolution, startup behaviour, index creation,
migration history, locking, idempotency, recovery, and how to add a structure.

**Gate.** Implementation gate. Live-stack rule applies — verify against the real Mongo instance that a second startup
reuses structures and creates nothing.

**Commit.** `feat(platform): add configuration-driven system store bootstrap`

---

## Phase 4 — Bootstrap correctness

**Objective.** Application startup stops depending on development and test tooling. **Topology changes are
deliberately deferred to Phase 25** — changing Compose before the modules it starts have been deleted means
touching it twice.

**Two verified defects to fix here.**

**4a. SQL migrations 003 and 004 never run.** `compose.yaml:203-204` explicitly invokes `001` and `002` only.
Replace the enumerated `sqlcmd -i` lines with an ordered migration runner that discovers `NNN_*.sql`
lexicographically, records applied versions in a platform-owned table, and never reruns an applied migration.
Applying 003/004 for the first time will create real objects — verify against the live SQL Server instance and
confirm `platform.bay_configuration` / `bay_reservation` / `bay_assignment` land correctly before commit.

**4b. `backend` blocks on `seed-runner`.** Remove `seed-runner` from `depends_on` on `backend`,
`return-workflow-worker`, and `return-orchestrator` (`compose.yaml:404,423,433`). Replace with dependencies on
`runtime-configuration-init`, `mongodb-rs-init`, `sqlserver-init`, `neo4j`, `temporal`, `valkey` as
appropriate. `seed-runner` itself stays for now — Phase 25 moves it to the `dev-tools` profile.

**4c. Three dependency resolution paths, none sharing a lock (D8).** `backend/Dockerfile:13` runs
`pip wheel .` from `pyproject.toml` with **no lockfile at all** — the container is not reproducible against
either lock. Bash host scripts use `poetry install --sync` (`poetry.lock`); PowerShell host scripts prefer
`uv sync --frozen` (`uv.lock`). Migrate all consumers to uv:

| Consumer | Target |
|---|---|
| `backend/Dockerfile` | `uv sync --frozen --no-dev` against a copied `uv.lock` |
| `scripts/bootstrap_host.{sh,ps1}` | `uv sync --frozen --all-groups` |
| `scripts/run_*_host.{sh,ps1}` | `uv run …` |
| phase gates | `uv run ruff` / `uv run mypy` / `uv run python -m compileall` |

`poetry.lock` and the `[tool.poetry]` block stay until a container build **and** a host bootstrap both succeed
from `uv.lock`; they are deleted in Phase 27. This is the one case where keeping the duplicate temporarily is
correct.

**Normal startup must not** reseed business data, rebuild the graph, perform live AI provider validation,
recreate valid collections, or reset configuration. Audit the lifespan in `main.py` for each of these and fix
what violates it.

**Preserve.** Vault, Platform MongoDB, Neo4j, Valkey, Temporal, Temporal PostgreSQL, local SQL Server support,
Dockerfiles, `compose.yaml`, `.env.example`, Neo4j migrations, SQL migrations, Vault bootstrap.

**Docs.** `README.md`, infra README, scripts README, `.env.example` comments.

**Gate.** Implementation gate + infrastructure checks. **Live-stack rule applies** — manual clean-bootstrap
run and a restart run against the Docker stack, verifying 003/004 objects land and that nothing reseeds.

**Commit.** `refactor(bootstrap): decouple application startup from test tooling`

---

# Track 2 — Agents and orchestration

## Phase 5 — Independent agent plugin contract

**Objective.** All agents become replaceable plugins.

**Current state.** Two registries. `agents/registry.py` is a frozen dataclass constructing six concrete agent
classes — no indirection, no configuration. `dynamic_knowledge/agents/registry.py`'s
`IndependentAgentRegistry` has the right semantics (unique `agent_id`, `task_queue`, `state_namespace`;
`prompt_ref`, `policy_ref`, `capabilities`, `max_concurrency`, `requests_per_minute`,
`circuit_breaker_failure_threshold`) but is descriptor-only — no `implementation_id`, no `enabled`, no
resolution to an executable.

**Work.** Create `agents/contracts/`, `agents/registry/`, `agents/README.md`.

Extend `IndependentAgentDescriptor` with `implementation_id`, `enabled`, `timeout`, `retry_policy`,
`ai_route_ref`, and typed input/output contract references. Merge the two registries into one that resolves
`agent_id → configured implementation_id → AgentPlugin`.

```python
class AgentPlugin(Protocol):
    @property
    def descriptor(self) -> AgentDescriptor: ...
    async def execute(self, request: AgentRequest,
                      context: AgentExecutionContext) -> AgentResult: ...
```

**`AgentExecutionContext` carries platform services and the capability registry — nothing module-specific.**
There is no `.ai` and no `.knowledge` field (design R2a, §13.1). Getting this right *here* matters: Phase 5
establishes the foundational agent API, and a context with named module services would force a second
refactor in Phase 5A and Phase 7.

```python
class AgentExecutionContext:
    configuration: RuntimeConfigurationView      # PINNED view, not the handle (Phase 1A, design §7.1)
    capabilities: CapabilityRegistry
    audit: AuditSink
    redactor: Redactor
    principal: Principal
    correlation_id: str
    session_id: str
    configuration_release_id: str
    consistency: ConsistencyHandle | None        # platform-neutral; threaded from the caller
    clock: Clock
    # deliberately absent: any domain type, any module service, any other agent
```

**A view, not a handle** (Phase 1A contract). An agent always executes for a session pinned to one release. Handing it a
`RuntimeConfigurationHandle` would let it call `current()` and read a release activated after the session
started — mixed business rules inside one return. The orchestrator resolves
`handle.pinned(session.configuration_release_id)` at the boundary and passes the resulting view; because the
view is the only object with `section()`, reading a newer release is not expressible.

**`consistency`, not `generation`.** `graph.lifecycle.GenerationHandle` is a domain type the shared agent
contract must not name, and five of the six agents never touch graph knowledge at all. The caller acquires
the handle at the operation boundary and passes it as a `ConsistencyHandle`; `GenerationHandle` structurally
satisfies that protocol.

Agents declare the shapes they need in `agents/contracts/ports.py` and resolve them per execution:

```python
class AgentAiPort(Protocol):
    async def invoke(self, task_id: str, inputs: Mapping[str, object]) -> AiOutcome: ...

class KnowledgePort(Protocol):
    async def query(self, request: KnowledgeRequest,
                    consistency: ConsistencyHandle) -> KnowledgeResult: ...
```

Order Discovery — the only agent needing generation-aware reads — narrows this further in its own package
(`agents/order_discovery/ports.py`, `KnowledgeConsistencyPort`), keeping generation semantics entirely outside
the shared contract. It resolves its ports from `context.capabilities`; the concrete binding to `ai.gateway`
and `graph.query` is constructed in `bootstrap/adapters/`. No agent package imports `ai` or `graph`.

**Agent configuration** (promoted from `config/v2/agents/`, per D1 and D4):
```yaml
agents:
  order_discovery:
    implementation: built_in.dynamic_order_discovery
    task_queue: returns.order-discovery
    state_namespace: order_discovery
    prompt_ref: ORDER_AGENT_REASONING_V1
    ai_route_ref: ORDER_AGENT_REASONING
    enabled: true
    max_concurrency: 20
```
No direct provider or model reference anywhere in agent configuration.

**Migrate six agents** — Order Discovery, Order Analysis, Return Workflow, Return Fulfillment, Bay Assignment,
Feedback Learning. Adapt existing implementations; do not rewrite working logic.

**Docs.** Each agent directory gets a README covering responsibility, input, output, queue, state, prompt,
policy, AI route, knowledge access, side effects, failure semantics, configuration, and
extension/replacement. Each states explicitly: *This agent does not directly invoke another agent.*

**Gate.** Implementation gate + import-cycle scan. Architecture tests added here and kept:
`tests/agents/test_no_cross_agent_imports.py`, `tests/agents/test_context_has_no_module_fields.py`.

**Commit.** `refactor(agents): standardize independent agent plugin contract`

---

## Phase 5A — LangGraph durable reasoning foundation

**Objective.** Introduce LangGraph without coupling any business module to it. Nothing uses it yet; Phases 7
and 9–11 consume what this phase builds.

**Dependencies.** Add `langgraph` and `langgraph-checkpoint` to `backend/pyproject.toml`, resolved through
`uv.lock` (D8 — no second dependency manager; pin the version at implementation time). **Do not add**
`langchain-openai`, `langchain-anthropic`, `langchain-google-genai`, or any other provider integration
package — a LangGraph node calling a provider directly would create a second AI routing path bypassing
failover, rate limits, circuit breakers, interception, replay, safety, and metrics (D11.6). Their absence is
asserted at the dependency level.

**Implement.**
```
platform/reasoning/
    checkpoint.py       SystemStoreCheckpointSaver
    thread_ids.py       ReasoningThreadIdFactory
    receipts.py         ReasoningActionReceipts
    retention.py        CheckpointRetentionPolicy
    redaction.py        checkpoint-state allowlist enforcement
    observability.py    reasoning-run trace emission
    errors.py           typed reasoning outcomes
    README.md
```

**Checkpointer.** `SystemStoreCheckpointSaver` implements LangGraph's `BaseCheckpointSaver` and resolves
storage as `SystemStore → logical structure → configured physical collection`. `InMemorySaver` / `MemorySaver`
are forbidden outside unit tests. If an existing LangGraph checkpoint implementation is adapted rather than
written from scratch, its collection names must still come from the manifest — an implementation that cannot
be told its collection names is not acceptable under rule 3.3.

**System-store structures** (added to `backend/config/platform/system_store.yaml` — the `encrypted` support
built in Phase 3 is the prerequisite):
```yaml
reasoning_checkpoints:        { physical_name: platform_reasoning_checkpoints, encrypted: true }
reasoning_checkpoint_writes:  { physical_name: platform_reasoning_checkpoint_writes, encrypted: true }
reasoning_runs:               { physical_name: platform_reasoning_runs }
reasoning_action_receipts:    { physical_name: platform_reasoning_action_receipts }
reasoning_resume_commands:    { physical_name: platform_reasoning_resume_commands }
```
Any additional structure a checkpoint implementation needs is declared here too — never library-created.

**Retention is keyed to terminal state, never creation time.** A fixed TTL from creation would delete the
checkpoints of a still-resumable run — an Analyzer clarification or a paused return can legitimately stay open
longer than any retention window, and losing its checkpoints makes the thread unresumable, defeating the whole
point of durable reasoning.

```
lifecycle_state RUNNING | INTERRUPTED | WAITING           → expires_at = null (never expires)
lifecycle_state COMPLETED | FAILED | CANCELLED | ABANDONED → expires_at = terminal_at + retention
```

A Mongo TTL index ignores documents with a null/absent `expires_at`, so this needs no special-casing. On the
terminal transition, `retention.py` stamps `expires_at` across **all three** structures together — checkpoints,
writes, and receipts. A receipt must never outlive-or-predecease the execution it protects, so the three
always share one expiry.

**Abandonment sweeper.** "Live runs never expire" would grow without bound when a user simply never answers a
clarification. A sweeper moves idle `INTERRUPTED`/`WAITING` runs to `ABANDONED`, starting the retention clock.
Abandonment is a business event: audited and visible in the AI Control Center, never a silent deletion.

**Abandonment must not race pending external work.** `PENDING_EXTERNAL` means a run is waiting on something
that can still complete. Abandoning on a bare idle timer would let an operator answer an interception on day
31 and have the resume worker deliver to a run already heading for deletion — a stuck workflow or a late
invalid resume. The sweeper therefore **skips** a run unless all of these hold:

```
no unresolved clarification interrupt
no receipt in STARTED or PENDING_EXTERNAL
no open AI interception referencing this run
no resume_command in PENDING
no active Temporal wait bound to this reasoning run
```

A run idle past the threshold but failing a precondition is flagged `ABANDONMENT_BLOCKED` with the blocking
reference listed and surfaced for operator action — unbounded growth becomes a visible queue, not a silent
hazard.

**Forced abandonment: one Mongo transaction, then a durable signal.** An operator may abandon a blocked run
explicitly. **The Temporal signal cannot join a Mongo transaction**, so claiming one atomic step across both
would be false — a crash after commit but before the signal strands the workflow forever. Same outbox
discipline as §13.5:

```
── Mongo transaction ──
   run.lifecycle_state                 → ABANDONED
   open interceptions                  → CANCELLED
   STARTED / PENDING_EXTERNAL receipts → FAILED_FINAL (REASONING_ABANDONED)
   expires_at                          → stamped on checkpoints, writes, receipts
   reasoning_resume_commands           → INSERT { command_id, status: PENDING,
                                                  workflow_id, run_id,
                                                  signal: REASONING_ABANDONED }
── COMMIT ──  →  resume worker delivers at-least-once  →  command DELIVERED
```

Crash after commit, before signal: the worker finds it and delivers. Crash after signal, before marking
`DELIVERED`: redelivered, deduplicated workflow-side on `command_id`. `reasoning_resume_commands` is a
separate logical structure because the command belongs to the run, not to any one interception — and this
transaction already spans several documents, so there is no single-document atomicity to preserve. (The
interception path keeps its *embedded* command precisely because that one is a single-document write.)

The workflow decides what an abandoned run means for the business session — it is never left waiting on a
signal that can no longer arrive.

**Late external completion is rejected, never resumed.** Every resume path re-reads `lifecycle_state` first.
A completion arriving for an `ABANDONED` run is refused, audited, and reported to the submitting operator.
The same guard prevents a superseded `GenerationChanged` attempt from being resumed.

```yaml
reasoning:
  checkpoint_retention:
    active_runs_expire: false          # not settable to true in production
    terminal_retention_hours: 168
    abandon_after_hours: 720
```

**Thread IDs — a thread is one reasoning attempt, not one conversation.** Reusing
`order-discovery:<conversation_id>` across turns carries `final_result`, `candidate_refs`, `query_budget`,
`search_plan`, and a stale `clarification` into the next turn, making correctness depend on every state field
being perfectly reset — an unverifiable hidden invariant.

```
Order Discovery        order-discovery:<conversation_id>:<turn_id>:<attempt>
Graph Schema Analyzer  graph-schema:<analysis_id>
```

| Event | Thread |
|---|---|
| new business turn | **new** — new `turn_id`, `attempt = 1` |
| clarification answer to an open interrupt | same |
| Temporal retry / interception resume / backend restart | same |
| `GenerationChanged` restart | **new** — same `turn_id`, `attempt + 1`; superseded attempt abandoned |

`turn_id` is allocated by the canonical conversation write, not inside the agent, so it is stable across
Temporal retries. Routing is explicit, not inferred from message shape: if the session has an outstanding
interrupt on its current thread, input is a resume; otherwise it starts a new turn. Conversation memory always
comes from canonical SystemStore state, never from a previous turn's LangGraph working state.

**The Analyzer is deliberately not per-turn** — an `AnalysisSession` is itself the unit of work and every
state field is analysis-scoped. Do not "fix" it symmetrically.

**Idempotency receipts are a state machine, not a cached value.** LangGraph re-executes an interrupted node
**from its beginning** on resume, so any side effect before the interrupt runs again. A naive
"record the result, return it on a hit" design **livelocks the interception path**: an intercepted AI call
returns `InterceptionPending`, that gets cached as the action's result, and every subsequent resume replays
the pending marker and interrupts again — forever, even after the operator has answered.

```
STARTED ──► COMPLETED | PENDING_EXTERNAL | FAILED_RETRYABLE | FAILED_FINAL
PENDING_EXTERNAL ──► COMPLETED | FAILED_FINAL     (resolved via external_ref)
```

`PENDING_EXTERNAL` is **never terminal and never returned as a result**. It records `external_ref` — the
`interception_id` for an intercepted AI call, the `sync_run_id` for a targeted sync — and a resumed node
resolves through that reference:

```
receipt lookup →
  none              → write STARTED, then act
  STARTED           → resolve by external_ref (never blind re-execute); unresolvable and target
                      not idempotent → FAILED_RETRYABLE, operator-visible
  PENDING_EXTERNAL  → resolve external_ref: still pending → interrupt again (no new side effect)
                                            completed     → fetch validated result → COMPLETED → continue
  COMPLETED         → return result_ref, no side effect
  FAILED_RETRYABLE  → re-attempt under a new attempt number
  FAILED_FINAL      → raise the typed outcome
```

`STARTED` is written **before** the action with the deterministic external key, so a crash mid-action is
resolvable rather than ambiguous. This also gives replay and manual response a deterministic path back to the
originating reasoning action: `interception_id → external_ref → (reasoning_run_id, node_name,
logical_action_id)`.

Required for: AI Gateway invocation, targeted sync requests, conversation updates, checkpoint-adjacent
persistence, and audit events.

**Checkpoint content allowlist.** `redaction.py` **rejects** — does not silently strip — any state key outside
the component's declared allowlist, so a violation surfaces in development rather than becoming a quiet
data-shape change. Never in checkpoints: Vault secrets, database passwords, API keys, credential-bearing
connection strings, raw configuration snapshots, raw unredacted source documents, large customer records,
provider authentication headers. Use references instead: `configuration_release_id`, `graph_generation_id`,
`source_snapshot_id`, `evidence_ref`, `query_execution_id`, `candidate_id`, `schema_revision_id`.

**Ownership boundary.** No business reasoning here — no Order Discovery graph, no Analyzer graph. Checkpoints
are reconstructible reasoning position, never the authoritative business record; canonical state stays in
`ReturnSession`, `Conversation`, `AnalysisSession`, `GraphSchemaDraft`, `ConfigurationRelease`.

**Configuration.** Add `backend/config/reasoning.yaml` (`enabled`, `checkpoint_store: SYSTEM_STORE`,
`checkpoint_encryption`, `checkpoint_retention.*` as above, `execution.bounded`).

**Docs.** `platform/reasoning/README.md` + root README architecture section.

**Gate.** Implementation gate. Architecture tests built here and kept:
`tests/reasoning/test_checkpoint_uses_system_store.py`,
`tests/reasoning/test_checkpoint_contains_no_secrets.py`,
`tests/reasoning/test_no_langchain_provider_packages.py`,
`tests/reasoning/test_langgraph_not_in_public_api.py`.
Focused checks (mechanism specifications, per §5.1):
`tests/reasoning/test_checkpoint_survives_restart.py`,
`tests/reasoning/test_active_run_checkpoints_never_expire.py`,
`tests/reasoning/test_pending_external_receipt_resolves.py` — asserts a `PENDING_EXTERNAL` receipt does not
livelock and does resolve to `COMPLETED` once the external reference closes,
`tests/reasoning/test_abandonment_blocked_by_pending_external.py`,
`tests/reasoning/test_forced_abandonment_commits_resume_command_atomically.py`,
`tests/reasoning/test_crash_after_abandonment_before_temporal_signal_recovers.py`,
`tests/reasoning/test_duplicate_abandonment_signal_is_idempotent.py`,
`tests/reasoning/test_late_completion_after_abandonment_rejected.py`.

**Commit.** `feat(platform): add LangGraph durable reasoning foundation`

---

## Phase 6 — Configuration-driven return orchestration

**Objective.** Move business sequencing out of agent implementations.

**Current state.** `config/v2/workflows/return_session.yaml` already declares stages
(`ORDER_DISCOVERY → ORDER_SELECTION → FULL_ORDER_SYNC → LINE_CONFIRMATION → ORDER_ANALYSIS → RETURN_REQUEST →
FULFILLMENT`) and the invariants `context_only_handoffs: true`, `direct_agent_calls_allowed: false`. Nothing
reads it. Sequencing lives in `workflows/production_return_workflow.py` and `operations/orchestrator.py`.

**Orchestrator responsibilities.** Load pinned workflow definition, transition state, invoke configured agent,
persist result, retry/time out, wait for human action, wait for integration event, advance, complete. It
performs no business reasoning that belongs to an agent.

**Workflow configuration.**
```yaml
workflow:
  return_session:
    stages:
      - { id: DISCOVERY,       handler: { type: AGENT, agent: order_discovery } }
      - { id: ANALYSIS,        handler: { type: AGENT, agent: order_analysis } }
      - { id: RETURN_DECISION, handler: { type: AGENT, agent: return_workflow } }
      - { id: SUPPORT,         optional: true,
                               handler: { type: HUMAN_WORK_QUEUE, queue: support } }
      - { id: FULFILLMENT,     handler: { type: AGENT, agent: return_fulfillment } }
      - { id: WAREHOUSE,       conditional: true,
                               handler: { type: AGENT, agent: bay_assignment } }
      - { id: FEEDBACK,        handler: { type: AGENT, agent: feedback_learning } }
```

**Conditions.** No general expression language. Allowlisted condition identifiers resolved by configured rule
implementations only.

**Temporal.** Stays as the durable orchestration engine. Convert the existing workflow progressively — do not
build a second orchestration system beside it.

**Reconciliation note.** The existing 7-stage `return_session.yaml` and this 7-stage list are not the same
stages. Produce an explicit mapping in the phase commit; the canonical business stage list in P16 is the
authority.

**Gate.** Implementation gate + workflow-config validation. Architecture test added here:
`tests/api/test_no_generic_advance_endpoint.py` (design §13.9).

**Commit.** `refactor(workflow): make return orchestration agent-independent and config-driven`

---

## Phase 7 — Order Discovery consolidation

**Objective.** The dynamic graph-first agent becomes the canonical Order Discovery implementation, with its
reasoning restructured as an explicit bounded LangGraph state machine behind an unchanged `AgentPlugin`
contract.

**Preserve.** `ActiveSchema`, the Neo4j knowledge gateway, progressive search *behaviour*, conversation
memory, query planner/compiler, graph safety, strong-anchor validation, hallucination guard, response safety,
AI Gateway routing.

**The coordinator does not survive as a coordinator (D11).** `DynamicOrderAgentCoordinator` is an oversized
procedural reasoning state machine with no durable internal checkpoints. It **decomposes into LangGraph
nodes**; `plugin.py` becomes a thin façade that resolves ports, acquires the `GenerationHandle`, and starts or
resumes the graph. Externally `AgentPlugin.execute(...)` is unchanged and no LangGraph type appears in any
public signature.

```
agents/order_discovery/
├── plugin.py          thin AgentPlugin façade
├── contracts.py  state.py  conversation.py  anchors.py  guards.py  prompt_policy.py
└── reasoning/         state.py  graph.py  nodes.py  routing.py  tools.py  limits.py  README.md
```

**Graph** (design §14.3):
```
START → LOAD_CONTEXT → UNDERSTAND_REQUEST → PLAN_SEARCH → QUERY_GRAPH → EVALUATE_RESULTS
  sufficient            → RANK_CANDIDATES → VERIFY_RESULT → RESPOND
  needs_more_search     → PLAN_NEXT_QUERY → QUERY_GRAPH
  needs_aggregation     → AGGREGATE → EVALUATE_RESULTS
  graph_miss            → CHECK_STRONG_ANCHOR → TARGETED_SYNC → QUERY_GRAPH
  clarification_required→ INTERRUPT_FOR_CLARIFICATION → RESUME → UNDERSTAND_REQUEST
```

**State is typed, bounded, reference-based.** `reasoning_run_id`, `conversation_id`, `session_id`,
`configuration_release_id`, `graph_generation_id`, `user_turn_ref`, `intent`, `strong_anchors`,
`search_plan`, `query_attempt`, `query_budget`, `query_execution_refs`, `candidate_refs`, `candidate_scores`,
`clarification`, `targeted_sync_requested`, `final_result`, `failure`. **No source records in state.**

**Bounded autonomy** — validated configuration in `agents/order_discovery.yaml`:
`max_steps: 20`, `max_graph_queries: 8`, `max_targeted_syncs: 1`, `max_clarifications: 3`, `max_replans: 5`.
Exhaustion raises `ReasoningLimitExceeded`; it never fabricates a result.

**Never block a Temporal worker on a human (design §14.8).** Two suspension causes — LangGraph `interrupt()`
for clarification, and `InterceptionPending` from the AI Gateway — follow one protocol: checkpoint persists,
the plugin returns `CLARIFICATION_REQUIRED` or `AI_INTERCEPTION_PENDING`, **the activity completes and the
worker is released**, the workflow records a waiting state, and a later activity resumes the *same*
`thread_id`. An `InterceptionPending` raised inside a node maps to a graph interrupt — never swallowed, never
retried in place.

**Temporal retry reuses the thread ID.** A retried activity must not open a new reasoning thread for the same
failed invocation.

**Generation consistency (composes with §13.4).** Acquire one `GenerationHandle` *before* invoking reasoning
and thread it into every knowledge-touching node as a `ConsistencyHandle`; LangGraph never resolves the
current generation. On `GenerationChanged`: abort the branch, release the handle, acquire a fresh one,
increment `attempt` — which yields a **new `reasoning_run_id` and a new thread**, so no generation-derived
state can survive and action receipts do not suppress legitimate re-execution — abandon the superseded
attempt (revoking its outstanding external work), and re-enter at `LOAD_CONTEXT`. `conversation_id` and
`turn_id` are unchanged, so business continuity comes from canonical conversation state rather than checkpoint
reuse. Reasoning never continues across generations and never resumes a superseded attempt.

**Thread scope is per turn.** `order-discovery:<conversation_id>:<turn_id>:<attempt>` — a new associate turn
gets a clean thread; a clarification answer, Temporal retry, interception resume, or restart reuses the
existing one. Routing is explicit: an outstanding interrupt on the current thread means the input is a resume,
otherwise it starts a new turn.

**Idempotency.** Every side-effecting tool keys on `reasoning_run_id + node_name + logical_action_id`. A
resumed node must never produce a second targeted sync.

**No cross-agent subgraphs.** The reasoning graph may not invoke another agent, directly or as a subgraph.
Cross-agent sequencing stays exclusively Temporal → Return Session Orchestrator → Agent Registry.

**Work.**
1. Sweep the Order Agent path (`dynamic_knowledge/order_agent/`, `dynamic_knowledge/api/order_agent.py`,
   `dynamic_knowledge/knowledge/`) for hardcoded table names, collection names, field names, graph labels, and
   relationship names. Replace every business-specific value with an `ActiveSchema` reference. Internal system
   collection *logical* names via `SystemStore` are permitted.
2. Finish on-demand sync wiring:
   `graph lookup → insufficient result → strong-anchor guard → schema-authorized targeted source read →
   dynamic projection → active graph generation → rerun original graph query`.
   All selected source assets and fields come from `ActiveSchema`/configuration.
3. Legacy `operations/order_discovery/` keeps its shared return/session/locking behaviour until P16 absorbs it,
   but its legacy discovery implementation stops being used.

**Docs.** `agents/order_discovery/README.md`, graph README, root architecture.

**Gate.** Implementation gate + dependency scan. **Live-stack rule applies** — this is field-path resolution
territory, the exact area where fakes have hidden real bugs in this repo.

Focused checks built here (mechanism specifications, per §5.1):
`tests/agents/test_order_discovery_reasoning_resume.py`,
`tests/agents/test_order_discovery_clarification_interrupt.py`,
`tests/agents/test_order_discovery_targeted_sync_idempotency.py`,
`tests/agents/test_order_discovery_generation_change_restart.py`,
`tests/agents/test_new_turn_does_not_reuse_previous_reasoning_state.py`,
`tests/agents/test_clarification_resume_reuses_same_reasoning_run.py`.
Architecture tests kept: `tests/reasoning/test_bounded_reasoning.py`,
`tests/reasoning/test_nodes_do_not_construct_ai_providers.py`,
`tests/reasoning/test_no_cross_agent_subgraphs.py`.

**Commit.** `refactor(order-discovery): consolidate on graph-first agent with durable reasoning`

---

# Track 3 — Sources and graph

## Phase 8 — Canonical source connector framework

**Objective.** One read-only source abstraction shared by Configuration and the Graph Schema Analyzer.

**Current state — four competing abstractions.** `data_console/api/sources.py`,
`api/data_source_config_v2.py`, `data_platform/sources/` (MongoDB only), and
`dynamic_knowledge/connectors/` (`mongodb.py`, `sqlserver.py` — the most mature, and the only ones proven
against real seed data).

**Contract.** `SourceConnectorPlugin` — `validate_connection`, `discover_namespaces`, `discover_datasets`,
`describe_dataset`, `sample_records`, `scan_records`, `current_watermark`. Mutating methods are intentionally
absent (rule 4.4).

**Registry.** Resolves `source.connector_type → registered connector implementation`.

**Implementations.** Migrate `dynamic_knowledge/connectors/mongodb.py` and `sqlserver.py` as the canonical
pair. Add PostgreSQL **only** if an implementation already exists or is explicitly required — never advertise
an unimplemented connector.

**Source configuration.** `source_id`, `connector_type`, connection metadata, `credential_ref`, `enabled`,
metadata discovery policy, sampling limits, query limits, timeouts. No `READ_WRITE` mode for external business
sources.

**Migration.** Progressively point the four existing abstractions at this registry. Delete no callers in this
phase.

**Gate.** Implementation gate + configuration reference scan. **Live-stack rule applies.**

**Commit.** `refactor(sources): unify read-only source connector framework`

---

## Phase 9 — Graph Schema Analyzer module

**Objective.** Replace the partial schema-design implementation with an independent production module.

**Correction to the draft plan.** The existing `SchemaDesignService` is not purely in-memory.
`self._contexts` is a dict (`v2/services.py:647`), but `V2PlatformServices.bind_state_store` /
`persist_all` snapshot the whole `schema_design` namespace into `MongoV2StateStore` with optimistic revisions.
The defect is **granularity** — one CAS document for every session, so concurrent sessions contend and
partial writes are impossible. The fix is per-entity persistence, not "add persistence".

**New module.**
```
graph_schema_analyzer/
├── domain/
├── application/
├── reasoning/          (added in Phase 10 — LangGraph)
├── ports/              the module's ENTIRE outward surface
├── persistence/
├── api/
├── module.py
└── README.md
```

**There is no `adapters/` package here.** The Analyzer declares Protocols in `ports/` and resolves them from
the `CapabilityRegistry` during `resolve_capabilities()`. Binding those ports to
`configuration.sources.registry`, `ai.gateway`, and `graph.lifecycle` happens in `bootstrap/adapters/`, the
only package permitted to import two modules. An `adapters/` directory inside this module would reintroduce
exactly the compile-time coupling the capability registry exists to eliminate (design §13.1).

**Independence.** The Analyzer imports **no other module** — not Return Business Copilot, the Return Workflow
Agent, Order Discovery, or Support, and not `configuration`, `ai`, or `graph` either. It depends only on
`platform.*` contracts and its own `ports/`.

**Persistent state.** Per-entity documents through SystemStore logical collections, not a namespace blob:
`analysis_session`, `source_snapshot`, `clarification`, `schema_draft`, `schema_revision`,
`validation_result`, `approval`.

**Snapshots must be classified (design §13.6).** A `SourceSchemaSnapshot` can contain live customer data.
Metadata (names, types, cardinalities) is always plaintext and always retained. Samples carry a
`sample_classification`:

| Value | Meaning |
|---|---|
| `NONE` | metadata-only; samples used transiently in the AI call, never persisted |
| `REDACTED` | persisted after `platform/secrets/redaction.py` — the default when sampling is on |
| `ENCRYPTED` | raw samples in `source_samples`, encrypted, with a mandatory `expires_at`; requires explicit opt-in on the source definition |

Architecture test built here: `tests/graph_schema_analyzer/test_independence.py` — asserts the module imports
nothing outside `platform.*` and itself, and that no `adapters/` package exists under it.

**APIs.** Versionless canonical domain:
```
POST /api/graph-schema/analyses
GET  /api/graph-schema/analyses/{id}
POST /api/graph-schema/analyses/{id}/messages
POST /api/graph-schema/analyses/{id}/validate
POST /api/graph-schema/analyses/{id}/approve
GET  /api/graph-schema/schemas
GET  /api/graph-schema/schemas/{id}
POST /api/graph-schema/schemas/{id}/build            body { activate: bool = true }
POST /api/graph-schema/schemas/{id}/generations/{gid}/activate
POST /api/graph-schema/schemas/{id}/sync
```

**Activation is generation-scoped and cannot bypass the lifecycle (design §13.9).** The activate endpoint
accepts only a generation already in `READY_FOR_ACTIVATION` — any other status is `409` and changes nothing —
and it invokes the orchestrator's own activation path (lease → CAS → `DRAINING` → `DrainController`), not a
reimplementation. It exists solely for the deferred case: `POST /build` with `{"activate": false}` stops at
`READY_FOR_ACTIVATION` so an operator can review deep-validation output first. The default runs the whole
sequence internally. `POST /sync` acquires a `GenerationWriteReservation` and fails rather than proceeding if
the generation moves.

**Gate.** Implementation gate + OpenAPI regeneration and commit (new router).

**Commit.** `feat(graph-schema): add independent persistent analyzer module`

---

## Phase 10 — Analyzer discovery and AI reasoning

**Objective.** The Analyzer actually analyses configured sources.

**Flow.** `user selects configured sources/datasets → Analyzer resolves connectors → read metadata → read
bounded samples when policy allows → build immutable SourceSchemaSnapshot → reasoning graph analyses → ask
clarification where required → produce GraphSchemaDraft`.

**Reasoning is a LangGraph state machine, not a linear call (D11).** `reasoning_service.py` becomes a thin
façade that starts, resumes, and inspects the graph; a single `snapshot + requirements → draft` call is too
linear for a loop that clarifies, proposes, validates, and revises.

```
graph_schema_analyzer/reasoning/   state.py  graph.py  nodes.py  routing.py  tools.py  limits.py  README.md
```
```
START → LOAD_SOURCE_SNAPSHOT → ANALYZE_STRUCTURE → IDENTIFY_GAPS
  clarification_required → INTERRUPT_FOR_CLARIFICATION → RESUME → ANALYZE_STRUCTURE
  sufficient_context     → PROPOSE_SCHEMA → VALIDATE_PROPOSAL
        validation_failure → REASON_ABOUT_FAILURES → REVISE_PROPOSAL → VALIDATE_PROPOSAL
        valid              → USER_REVIEW
              modification → APPLY_TYPED_MUTATION → VALIDATE_PROPOSAL
              accept       → READY_FOR_APPROVAL
```

**State holds IDs and metadata only** — `analysis_id`, `configuration_release_id`, `source_snapshot_id`,
`source_schema_hash`, `requirements`, `clarification_count`, `draft_id`, `revision_id`,
`validation_result_id`, `validation_attempt`, `reasoning_notes`, `next_action`, `completion_status`.
**No raw source samples in state or in interrupt payloads** — they stay under the `NONE` / `REDACTED` /
`ENCRYPTED` classification from Phase 9. Interrupt payloads carry `analysis_id`, `draft_id`, `revision_id`,
the question, and allowed structured choices.

**Reasoning stops at `READY_FOR_APPROVAL`.** It must never perform graph generation activation,
`ActiveRuntimeSnapshot` CAS, draining, retirement, configuration activation, or any source DDL/DML. Those
belong to `ApprovalService`, `BuildService`, `RebuildTrigger`, `GenerationLifecycleOrchestrator`, and
`DrainController`. `LangGraph = think / inspect / clarify / propose / revise / validate`;
`graph lifecycle = build / fence / activate / drain / retire`.

**Bounded autonomy** — `max_steps: 40`, `max_clarifications: 10`, `max_validation_revisions: 8`,
`max_source_tool_calls: 20`, `max_ai_calls: 15`. On exhaustion the run terminates at `NEEDS_HUMAN_REVIEW`,
never looping indefinitely.

**The Analyzer is still not an agent.** It gains a reasoning engine, not an `agent_id`, a task queue, or a
registry entry. The Return Session Orchestrator continues to know nothing about it.

**No hardcoding.** No `if collection == "orders"`, no `if table == "customers"`. Metadata and configuration are
the only schema source.

**Source samples are untrusted.** Structured AI context with hard separation:
```
SYSTEM POLICY
MODULE POLICY
ANALYZER TASK
SOURCE METADATA
UNTRUSTED SOURCE SAMPLE
USER REQUIREMENTS
```
Source values never become instructions. Reuse the existing prompt-injection defences in
`ai_gateway/safety.py` and the untrusted-data framing already used in `config/ai_gateway.yaml` task prompts.

**AI route.** Configuration-driven, promoted from `config/v2/agents/graph_schema_design.yaml` per D4:
```yaml
ai:
  tasks:
    GRAPH_SCHEMA_ANALYSIS:
      route: GRAPH_SCHEMA_REASONING
```
No hardcoded provider or model.

**Output.** The model may propose entities, properties, identifiers, relationships, cardinality,
transformations, search anchors, ownership, indexes, constraints, and sync semantics — for the graph only.

**Gate.** Implementation gate + schema-contract validation. **Live-stack rule applies** — run a real analysis
against the seeded `return_source` Mongo database and the SQL Server `return_platform` database.

Focused checks built here: `tests/graph_schema_analyzer/test_reasoning_resume.py`,
`tests/graph_schema_analyzer/test_clarification_interrupt.py`,
`tests/graph_schema_analyzer/test_reasoning_cannot_activate_generation.py`,
`tests/graph_schema_analyzer/test_reasoning_cannot_mutate_source.py`.

**Commit.** `feat(graph-schema): implement source-driven schema reasoning`

---

## Phase 11 — Analyzer interactive editing and validation

**Objective.** User and agent iteratively refine the graph safely.

**Typed operations.** Add/remove/rename entity; add/remove graph property; change identifier; add/remove
relationship; change cardinality; change source mapping; change transformation; add/remove graph index;
add/remove graph constraint; change ownership/sync rule.

**Hard boundary.** The model never generates executable database statements. It proposes structured schema
mutations; a compiler converts validated structures into graph operations.

**Validation before approval.** Source exists; dataset exists; field exists; type compatibility; identifiers
available; relationships resolvable; cardinality plausible; transformation supported; search anchors viable;
Cypher compilation successful; query safety successful; graph index definition valid; graph constraint valid;
sync projection executable.

**Source restrictions.** Validation may inspect the source. It may never propose or execute a source change.

**Reasoning integration.** The edit loop runs inside the graph built in Phase 10: `USER_REVIEW →
APPLY_TYPED_MUTATION → VALIDATE_PROPOSAL`, with `REASON_ABOUT_FAILURES → REVISE_PROPOSAL` on failure, bounded
by `max_validation_revisions`. Durable interruption covers missing-identifier clarification, relationship
ambiguity, cardinality ambiguity, user-requested schema review, and proposal review — each surviving a backend
restart on the same `graph-schema:<analysis_id>` thread.

**Gate.** Implementation gate + OpenAPI regeneration + frontend contract generation.
Focused check: `tests/graph_schema_analyzer/test_validation_revision_loop.py` — asserts the loop terminates
at `NEEDS_HUMAN_REVIEW` rather than running unbounded.

**Commit.** `feat(graph-schema): add interactive mutation and validation lifecycle`

---

## Phase 12 — Complete the graph generation lifecycle

**Objective.** Finish blue/green generation management.

**Current state — more built than the draft plan assumed, and less wired.**
`GenerationLifecycleOrchestrator.build_and_activate()` exists and has been validated against real
infrastructure: it drives `acquire lease → PREPARING → BUILDING → CATCHING_UP → VALIDATING →
READY_FOR_ACTIVATION → ACTIVE (Neo4j) → CAS the Mongo ActiveRuntimeSnapshot → retire previous → release lease`.
`MongoActiveRuntimeSnapshotStore` (real atomic CAS) and `MongoRebuildLeaseStore` exist.

**Three real gaps.**

**12a. Nothing calls it.** `grep` finds `GenerationLifecycleOrchestrator` only in its own module and its test.
Decide and build the rebuild trigger. Recommended: an authenticated admin endpoint on the Graph Schema
Analyzer API (`POST /api/graph-schema/schemas/{id}/build`, already specified in P9), with the orchestrator
resolved through the module registry. A scheduled trigger may be added later; an API trigger is the minimum.

**12b. No read leases, so no real drain — and ephemeral leases alone are insufficient (design §13.3).**
`lifecycle/orchestrator.py:8-15` documents the missing drain. But a return session can sleep for hours or
days, and `return_sessions` records a generation: request-scoped leases would let N drain and retire while a
suspended workflow is still bound to it. Build **three** lease kinds and gate retirement on all three:

| Lease | Scope | Timed out? |
|---|---|---|
| `GenerationReadLease` | one request | yes, drain timeout |
| `GenerationWriteReservation` | one sync run | yes, drain timeout |
| `GenerationSessionLease` | one workflow session, **durable, no TTL** | **never** |

`DrainController` moves `DRAINING → RETIRED` only when all three counts are zero. A durable session lease is
never force-expired — N stays `DRAINING` and an operational alert names the blocking sessions and offers
explicit rebind.

Also build `binding.py` with the two session modes. Default `REBIND_ON_RESUME`: the session records
`generation_id_at_start` for audit, holds no durable lease, and **revalidates its cached graph-derived facts**
on each resume, surfacing a stale fact as a stage-level conflict rather than silent drift. `PIN_STRICT` takes
the durable lease and accepts that N cannot retire until the session closes.

Tests built here: `tests/graph/test_drain_blocks_on_session_lease.py`,
`tests/graph/test_rebind_on_resume_revalidates.py`.

**12b-2. One generation handle per operation (design §13.4).** A request can read N, activation can flip to
N+1, and on-demand sync would then write to "the active generation" — the rerun executing against a different
generation than the original query. Build `lifecycle/handles.py`: a `GenerationHandle` acquired **once** at the
outermost boundary (HTTP entry, workflow activity start, sync run start) and threaded explicitly through
`query → miss → guard → targeted source read → projection → write → rerun`. `graph/sync/on_demand.py` takes it
as a **required argument** and no code below the acquisition point may reach the active-generation resolver.
`assert_current()` at each stage boundary; on `GenerationChanged` the operation restarts from the top with a
fresh handle under a bounded retry, never continuing on stale state.

Tests built here: `tests/graph/test_generation_handle_threading.py` (static — asserts no path from the
projector, writer, or on-demand sync to the resolver), `tests/graph/test_on_demand_restarts_on_generation_change.py`.

**Never destroy generation N immediately after activating N+1.**

**12c. Deep pre-activation validation is thin.** Before activation verify generation status, source coverage,
entity counts within configured expectations, relationship integrity, required identifiers, required indexes,
required constraints, known-query compilation and execution, absence of invalid orphan patterns, and sync
checkpoint consistency. **Validation queries come from the graph schema** — no hardcoded business node labels.

**Failure semantics.** If N+1 fails: mark `FAILED`, leave N `ACTIVE`, record the failure. Never leave a
candidate `ACTIVE`-but-unreferenced.

**Gate.** Implementation gate + configuration validation. **Live-stack rule applies** — run a real
`build_and_activate()` cycle and a forced-failure cycle against the Docker stack.

**Commit.** `feat(graph): complete safe generation activation and draining`

---

# Track 4 — AI

## Phase 13 — AI Control Center backend consolidation

**Objective.** All production AI operations in one independent module.

**Preserve** the existing AI Gateway concepts, which are sound: provider routing, model routing, task
configuration, fallback, rate limits, concurrency, timeouts, retries, circuit breakers, traces, metrics,
safety, replay, interception, audit. `config/ai_gateway.yaml` (343 lines, with per-task tier, prompt version,
system prompt, fallback strategy, token limits, allowed providers, and allowed input keys) is a genuine asset —
migrate it, do not redesign it.

**Work.** Consolidate `ai_gateway/` into `ai/` with `gateway/`, `providers/`, `routing/`, `safety/`,
`metrics/`, `interception/`, `api/`. Do not rewrite provider integrations that already work
(`anthropic.py`, `google.py`, `nvidia.py`, `ollama.py`, `openai.py`, `openai_compatible.py`, `http.py`).

**Provider plugin.** `AIProviderPlugin` — `capabilities`, `invoke`, `health`. Availability and configuration
come from configuration.

**Task routing.** Agents depend on an AI task ID, never a provider or model.

**Docs.** `ai/README.md` becomes the canonical AI architecture document.

**Gate.** Implementation gate + OpenAPI regeneration.

**Commit.** `refactor(ai): consolidate gateway into AI Control Center backend`

---

## Phase 14 — Durable interception service

**Objective.** Keep the manual-response capability; replace the file-polling architecture with a durable
backend service. The backend becomes authoritative; the script becomes a thin operator CLI over the same APIs
the AI Control Center UI uses.

**Current state.** `ai_gateway/providers/manual.py` writes request JSON into `.manual_llm/` and blocks in an
`asyncio.sleep` poll loop (`:27,82`). `backend/scripts/manual_llm_responder.py` (125 lines) watches the same
directory. Covered by `backend/tests/test_manual_ai_provider.py`, which must be migrated in this commit.

**Remove from the core architecture.** `.manual_llm` request directory, response directory, filesystem
polling, blocking provider request, manual filesystem provider.

**Implement.** `ai/interception/` — `models.py`, `repository.py`, `service.py`, `replay.py`,
`manual_response.py`, `policy.py`, `README.md`.

**Interception record.** `interception_id`, `trace_id`, `request_id`, `session_id`, `agent_id`, `task_id`,
`status`, `reason`, `configuration_release_id`, `configuration_checksum`, sanitized `route_metadata`,
`prompt_version`, `graph_generation_id`, `response_schema`, `envelope_ref`, `created_at`, `claimed_at`,
`completed_at`, `claimed_by`, `completed_by`, `response_origin`, `version`, and the embedded `resume_command`.

**No `configuration_snapshot` and no `route_snapshot` (design §13.6).** Both could persist resolved
credentials and customer data, and UI redaction does nothing about data already on disk. Persist release IDs
and checksums, and route provenance as `{task_id, route_id, provider_id, model_id, tier}` — never endpoints,
headers, keys, or anything Vault resolved. The replay envelope moves to `ai_request_envelopes`, **encrypted**
via Vault transit, gated behind a distinct `ai.replay.read` capability, TTL-expired per
`config/ai/interception.yaml`. `ai_traces` keeps metrics only — no prompt or response bodies.

Test built here: `tests/ai/test_no_secrets_in_interception_record.py`.

**State machine.**
```
PENDING → CLAIMED → { RESPONDED | REPLAYED | CANCELLED | RELEASED → PENDING }
PENDING → EXPIRED
```
Optimistic concurrency on `version`. Two operators cannot both successfully claim one interception.

**Completion and resume are one write (design §13.5).** Persisting `RESPONDED` and then resuming the caller
loses the resume on a crash between the two — the AI request is permanently complete and the workflow is stuck
forever; the reverse order duplicates responses. Embed the resume command **in the interception document**, so
completion is a single guarded update that sets the terminal status and `resume_command.status = PENDING`
together:

```
update ai_interceptions
where  interception_id = X and version = V and status = CLAIMED
set    status = RESPONDED, completed_at, completed_by,
       response_origin = HUMAN_INTERCEPTION,
       resume_command = { command_id, status: PENDING, workflow_id, run_id,
                          signal_name, result_ref, attempts: 0 },
       version = V + 1
```

Build `ai/interception/resume_worker.py` to deliver pending commands **at least once** with backoff, marking
`DELIVERED` only on acceptance. Workflow-side signal handling is **idempotent on `command_id`**. The same
pattern applies to `REPLAYED` and `CANCELLED`, which also unblock a caller.

Tests built here: `tests/ai/test_resume_command_atomicity.py`,
`tests/ai/test_resume_redelivery_is_idempotent.py`, plus the double-claim concurrency test.

**Manual response validation chain.** `response parsing → task output schema → agent contract → business
safety → hallucination/grounding checks where applicable → resume caller`.

**Provenance.** Record `response_origin = HUMAN_INTERCEPTION`, `provider = MANUAL_INTERCEPT`. A human response
is **never** attributed to Gemini, Claude, NVIDIA, or any other provider — in storage, in metrics, or in the UI.

**Replay.** Same immutable request + same route; same immutable request + alternate allowed route. The original
request envelope must be durable enough to reproduce the request under configured retention and security
policy. UI-visible payloads stay redacted.

**Operator-assisted generation.** `operator instruction → configured AI task → candidate → validation →
operator review/edit → validation → submit`. **Never auto-submit.**

**Convert the script.** `backend/scripts/manual_llm_responder.py` keeps its path but changes responsibility to:
authenticate, list pending interceptions, claim, show redacted context, submit manual response, request replay,
release/cancel. It contains no interception business logic.

**Gate.** Implementation gate + OpenAPI regeneration. Concurrency test for the double-claim case is mandatory in this
phase, not deferred.

**Commit.** `feat(ai): replace manual file provider with durable interception service`

---

# Track 5 — Configuration and business backends

## Phase 15 — Canonical Configuration backend

**Objective.** One configuration control plane.

**Canonical domain.** `/api/config/*` with subdomains `sources`, `integrations`, `business`, `runtime`,
`modules`, `security`, `releases`, `audit`.

**Data source flow.** `create definition → store secret in Vault → persist Vault reference → validate →
discover metadata → activate`.

**Credentials.** Secret values are never exposed through normal APIs. **Remove credential-reveal
functionality** wherever it exists (audit `api/data_source_config_v2.py` and `data_console/api/sources.py`).

**Runtime validation.** Migrate the useful validation functions out of
`data_console/api/runtime_validation.py`. Do not keep a separate Data Console runtime-validation product.

**Releases.** One lifecycle: `DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED`. Activation produces an
immutable runtime snapshot/version.

**Gate.** Implementation gate + OpenAPI regeneration + configuration validation.

**Commit.** `refactor(config): consolidate configuration control plane`

---

## Phase 16 — Return Business Copilot backend

**Objective.** One business domain covering the complete return lifecycle.

**Canonical stages** (authoritative — P6's workflow config reconciles to this list):
`DISCOVERY → ANALYSIS → RETURN_DECISION → SUPPORT → RMA → FULFILLMENT → PHYSICAL_RETURN → WAREHOUSE →
RESOLUTION → FEEDBACK`.

**Migrate, preserving functionality, from:** `api/returns.py`, `api/associate_returns.py`,
`api/return_support.py`, `api/support.py`, `api/production_workflow.py`, `api/physical_operations.py`,
`api/warehouse_placement.py`, `api/return_artifacts.py`, `api/return_agents.py`, plus
`operations/` (`associate_flow.py`, `orchestrator.py`, `production_workflow.py`, `feedback_service.py`,
`physical/`, `warehouse/`, `return_support/`).

**Consolidate the four verified duplications.**
- **Support** — `api/support.py` and `api/return_support.py` become one Support domain.
- **Conversations** — one durable conversation/session mechanism (currently split across `conversation/`,
  `dynamic_knowledge/order_agent/conversation_repository.py`, and operations session state).
- **Return state** — one canonical return/session aggregate.
- **Timeline** — one business-event timeline.
- **Artifacts** — one service for RMA/RGA data, label, tracking, BOL, shipping instructions.

**API shape.** `/api/returns/*` with nested `sessions`, `messages`, `actions`, `support`, `artifacts`,
`timeline`, `warehouse`, `feedback`. No V1/V2 naming.

**No generic advance endpoint (design §13.9).** A client-visible `POST /sessions/{id}/advance` would let a
caller skip an agent, a human queue, or an integration wait — contradicting "the orchestrator owns every
transition". Progress is event- and action-driven only: a message, an allowlisted structured action, a human
work-queue completion, or an inbound integration event. Each submits *intent*; the orchestrator evaluates
stage prerequisites and decides whether a transition occurs. `GET /sessions/{id}/actions` returns only actions
legal for the current stage and the caller's capabilities, and the backend re-checks regardless.

**Note.** `api/returns.py`, `api/physical_operations.py`, and `api/return_artifacts.py` all currently mount at
`prefix="/api/v1/returns"` — three routers sharing one prefix. Untangle this deliberately during migration.

**Gate.** Implementation gate + OpenAPI regeneration.

**Commit.** `refactor(returns): consolidate full return lifecycle backend`

---

# Track 6 — Frontend

## Phase 17 — RBAC foundation and four-domain shell

**Objective.** Final navigation, built alongside the legacy UI.

**Prerequisite the draft plan missed.** Phases 18–21 assume RBAC drives queue visibility, action availability,
field editability, and approval authority. Today `security/principal.py` defines only `Principal` and
`AuthorizationError` — the role model does not exist. **Build it in this phase**: role definitions, a
capability model, backend enforcement middleware, and a frontend capability hook. Do not defer this into the
screen phases; four screens will each invent their own otherwise.

**Second correction.** `App.tsx:85-87` redirects everything not under `/v1` or `/v2/...` to `/v1/...`. The new
routes are not reachable without editing that fallback, so this phase **must** modify `App.tsx`. The change is
additive in effect — legacy routes keep working — but it is not a no-op file.

**Routes.** `/returns`, `/config`, `/graph-schema`, `/ai`.

**Shell.** Only the four domain entries. RBAC hides unauthorized sections. No V1/V2 selector.

**Do not delete any legacy UI in this phase.**

**Gate.** Implementation gate, frontend + backend (RBAC middleware).

**Commit.** `feat(frontend): add unified four-domain application shell`

---

## Phase 18 — Return Business Copilot UI

```
┌──────────────────────────────────────────────────────────────┐
│ Return Business Copilot                                      │
│ Discovery > Analysis > Decision > RMA > Fulfillment > ...    │
├─────────────┬──────────────────────────┬─────────────────────┤
│ Queues      │ Conversation/Workspace   │ Return Context      │
│ My Returns  │ Agent/user messages      │ Customer / Order    │
│ Support     │                          │ Items / Decision    │
│ Warehouse   │ Structured actions       │ RMA / Tracking      │
│ Closed      │                          │ Warehouse / Resolution │
└─────────────┴──────────────────────────┴─────────────────────┘
```

**Detail drawer.** Timeline, Agent Activity, AI Calls, Graph Evidence, Integrations, Audit.

**Role behaviour.** One screen. RBAC (from P17) determines available queue, available action, editable fields,
and approval authority. Do not recreate separate support or warehouse applications.

**Reuse.** Components from `features/operations/` (15 files) and the shared `components/` directory move under
the new screen rather than being rewritten.

**Gate.** Implementation gate, frontend.

**Commit.** `feat(frontend): build end-to-end Return Business Copilot`

---

## Phase 19 — Configuration UI

**Tabs.** Overview, Data Sources, Integrations, Business, Runtime, Modules, Security, Releases, Audit.

**Data source detail.** Connection, Validation, Datasets, Schema, Data Preview, Usage, Audit. Data preview is
bounded and read-only.

Absorbs the useful Data Browser / Source / Inventory functionality from `features/data-console/` without
preserving Data Console as a product.

**Gate.** Implementation gate, frontend.

**Commit.** `feat(frontend): consolidate platform Configuration experience`

---

## Phase 20 — Graph Schema Analyzer UI

```
┌──────────────────────────────────────────────────────────────┐
│ Graph Schema Analyzer                                        │
├──────────────┬─────────────────────────┬─────────────────────┤
│ Sources      │ Graph Canvas            │ Analyzer Copilot    │
│ databases    │ entities                │ analysis            │
│ collections  │ relationships           │ clarification       │
│ tables       │ mappings                │ modification        │
└──────────────┴─────────────────────────┴─────────────────────┘
│ Properties | Mapping | Indexes | Validation | Sync | Versions │
└──────────────────────────────────────────────────────────────┘
```

**Actions.** Select sources, analyze, answer clarification, edit graph, chat modification, view diff, validate,
approve, build, activate, sync. **Never offer a source-side schema modification.**

**Gate.** Implementation gate, frontend.

**Commit.** `feat(frontend): rebuild independent Graph Schema Analyzer`

---

## Phase 21 — AI Control Center UI

**Tabs.** Overview, Requests, Interceptions, Metrics, Providers & Models, Routes & Tasks, Safety,
Configuration, Audit.

**Request inspection.** Trace, agent, task, provider, model, request timing, token metrics, retry/fallback,
prompt version, configuration version, graph generation, safety results, result.

**Reasoning runs** appear inside the existing request/module views — not on a separate screen:

| Order Discovery | Graph Schema Analyzer |
|---|---|
| reasoning run, current/last node, step count, graph queries, AI calls, clarification count, targeted sync count, final status | analysis reasoning run, current/last node, clarifications, proposal revisions, validation iterations, AI calls, final status |

**Never expose hidden chain-of-thought.** Show node and action names, tool activity, structured decisions,
validation results, and trace IDs — not model private reasoning text. LangSmith is not a production
dependency; platform observability and this screen stay authoritative.

**Interceptions.** Queue by Pending / Claimed / Completed / Expired. Actions: Claim, Respond Manually, Generate
Candidate, Replay Same Route, Replay Alternate Route, Release, Cancel.

**Manual response editor.** Renders the expected response schema and prevents structurally invalid responses at
the UI layer; the backend validates again regardless.

**Metrics.** Filter by provider, model, agent, task, route, status, time. Show requests, success rate,
failures, timeouts, latency, tokens, fallback, rate limits, circuit breaker, guard rejection, manual
intervention.

**Gate.** Implementation gate, frontend.

**Commit.** `feat(frontend): build AI Control Center and intervention console`

---

# Track 7 — Cutover and cleanup

*The draft plan cut over 20 routers in a single commit. That is split here into three reversible steps.*

## Phase 22a — Cut over the Data Console domain

Remove startup registration of the 17 `data_console` routers: `console_router`, `schema_catalog_router`,
`operational_generation_router`, `ai_studio_router`, `graph_sync_router`, `feedback_learning_router`,
`graph_router`, `graph_evidence_router`, `inventory_router`, `sources_router`, `browser_router`,
`workspaces_router`, `jobs_router`, `scenarios_router`, `audit_router`, `configuration_router`,
`copilot_operations_router`, `runtime_validation_router` (`main.py:880-898`).

Verify beforehand, per router: every frontend caller now targets `/api/config/*` or `/api/graph-schema/*`;
no worker, script, or configuration reference remains.

**Gate.** Implementation gate + import validation + OpenAPI regeneration and commit.
**Commit.** `refactor(runtime): cut over Data Console APIs to canonical modules`

## Phase 22b — Cut over V2 platform and studio surfaces

Remove `platform_v2_router`, `data_source_config_v2_router`, `ai_studio_router` remnants, and
`V2PlatformServices` construction from the lifespan (`main.py:368-371`). Configuration loading moves to the
canonical loader built in P2.

**Gate.** Implementation gate + import validation + OpenAPI regeneration and commit.
**Commit.** `refactor(runtime): retire V2 platform shell`

## Phase 22c — Cut over remaining legacy routers and finalize `main.py`

Remove `seed_router`, `dependency_simulator_router`, `dependencies_router`, and the legacy returns routers
superseded by P16.

**`main.py` final responsibility:** load bootstrap settings; initialize core platform; bootstrap system store;
load canonical configuration; construct the module runtime context; activate configured modules; mount module
routers; start lifecycle. It contains no business decision logic, no source-specific logic, no agent
construction detail, no graph schema logic, and no AI provider selection.

**Gate.** Implementation gate + import validation + OpenAPI regeneration and commit.
**Commit.** `refactor(runtime): reduce main.py to module activation`

---

## Phase 23 — Remove legacy frontend routes

Delete `/v1`, `/v2/config`, `/v2/copilot`, and all `/data-console/*` route registrations. Remove
`VersionOneApp`, `versioning.ts`, and the legacy redirect in `App.tsx`.

Delete: AI Studio, AI Simulator, Dependency Simulator (9 files), Operational Generation, Workspaces, Scenarios,
Seed Data, old Graph Explorer / Graph Sync / Graph Evidence screens, duplicate AI screens, duplicate Config
screens, duplicate support/logistics/warehouse apps. Reusable components move under the new screens.

Prune `frontend/src/contracts/*.ts` for the removed domains (`dataStudio.ts`, `dependencySimulator.ts`,
`browser.ts`, `inventory.ts`, `jobs.ts`, `graphExplorer.ts`, `consoleGovernance.ts` — verify each).

**Gate.** Implementation gate, frontend + dead route/import scan. Delete the corresponding tests in the same commit.
**Commit.** `refactor(frontend): remove legacy V1 V2 and studio surfaces`

---

## Phase 24 — Remove superseded backend implementations

**Per-candidate dependency scan before any deletion:** ripgrep imports, API route references, configuration
references, Compose references, frontend calls, worker references, documentation references. Delete only at
zero legitimate runtime consumers.

**Candidates.** `data_console/` shell; `data_platform/ai_studio.py`; `data_platform/operational_generation/`;
Workspaces; Scenarios; data-job infrastructure (`scripts/run_data_job_worker.py`);
`api/dependency_simulator.py` + `dependency_simulation/`; `api/seed.py` + `operations/seed_*.py`; `v2/` shell;
legacy `operations/order_discovery/`; duplicate support API; duplicate source registries;
`ai_gateway/providers/manual.py`; `SchemaDesignService`.

**Explicitly investigate before deciding — not in the draft plan.**
- `data_platform/graph/writer.py` + `commands.py` — a **third** Neo4j-writing system (Customer foundation
  slice, used by the operational-generation pipeline). If operational generation goes, this likely goes with
  it. It has never been investigated; do that before deleting or keeping.
- `data_platform/graph/schema.py` (`GraphSchema`/`GraphSchemaManager`) — deliberately retained earlier because
  `data_console/api/graph_sync.py`'s `/schema/apply` depends on it. Once that router is gone (P22a), re-evaluate.

**Explicit dispositions required** (the draft plan lists none): `canonical/`, `data_governance/`,
`validation/`, `conversation/`, `shared/governance.py`, `workflows/` (13 modules), `workers/`. Decide
keep/move/delete for each and record it in the commit message.

**Retain** internal seed/test fixtures under development/test ownership where still useful.

**Gate.** Implementation gate across the whole repository — format, lint, typecheck, compileall, import
validation, frontend build, OpenAPI regeneration. **No suite run** (D7): deletion safety comes from the
dependency scan above plus deleting each candidate's covering tests in the same commit, not from a green
suite. Residual breakage is found in Phase 30, by design.
**Commit.** `refactor(cleanup): remove superseded platform implementations`

---

## Phase 25 — Compose topology and bootstrap scripts

*Moved here from the draft's Phase 4 — the topology can only be finalized once the services' contents are gone.*

**Profile contract (D6).** `docker compose up` with no profile brings up infrastructure and bootstrap only.
The application tier is always an explicit opt-in.

| Profile | Services |
|---|---|
| *(default)* | `vault` `mongodb` `mongodb-rs-init` `neo4j` `valkey` `sqlserver` `sqlserver-init` `temporal-postgresql` `temporal` `runtime-configuration-init` |
| `containerized-app` | `backend` `return-workflow-worker` `return-orchestrator` `outbox-publisher` `frontend` |
| `dev-tools` | `temporal-ui` `seed-runner` `diagnostics` |

```bash
docker compose --profile containerized-app up -d
```
`scripts/start.{sh,ps1}` pass the profile explicitly and accept `--dev` to add `dev-tools`. No service relies
on the default set to be started.

**Resolve D3.** Compare `outbox-publisher` and `integration-outbox-worker` behaviour. Consolidate to one;
if they are not equivalent, merge the distinct functionality rather than dropping either.

**Remove** `data-job-worker` (its consumers are gone after P24).

**Bootstrap scripts.** Consolidate toward `scripts/bootstrap.{sh,ps1}`, `scripts/start.{sh,ps1}`,
`scripts/stop.{sh,ps1}`. Keep more specialized scripts only where they provide distinct operational value —
never delete an operational script merely because a similarly named one exists; consolidate its function first.

**Docs.** `README.md`, infra README, scripts README, `.env.example` comments.

**Gate.** Implementation gate + infrastructure checks + a real clean bootstrap and a real restart against Docker.
**Commit.** `refactor(bootstrap): finalize production compose topology`

---

## Phase 26 — Configuration and environment cleanup

Remove obsolete configuration only after the corresponding implementation is gone.

**Delete/migrate.** `config/dependency_simulation.yaml`; AI Studio configuration; Data Console configuration;
duplicate schema registries (`config/schema_registry.yaml` vs `config/data_platform/`); duplicate source
definitions (`config/data_platform/sources.yaml` vs `config/v2/sources/`); credential-reveal configuration;
obsolete V1/V2 flags. **`config/v2` is not deleted — it was promoted in P2** (D1); verify the promotion is
complete and remove only the empty husk.

**`.env.example`.** Bootstrap/deployment values only, each documented. No passwords, API keys, or Vault secret
values.

**Live AI validation.** Normal startup performs no provider/model validation. Explicit validation stays an AI
Control Center operation, and a recent successful validation receipt is reusable rather than re-calling
providers.

**Gate.** Implementation gate + configuration validation + startup import/config construction.
**Commit.** `refactor(config): remove obsolete legacy configuration`

---

## Phase 27 — Script and repository cleanup

**Preserve.** Bootstrap, start/stop, Vault, SQL migrations, Neo4j migrations, OpenAPI generation, contract
generation, lint/type/build helpers, `scripts/check_openapi_drift.py`.

**Remove.** One-time repair scripts, stage proof scripts, `scripts/generated-fixes/`, historical execution
handoff scripts, obsolete duplicate bootstrap scripts, and the ~20 `scripts/*stage4*` files
(`run_stage4m_*`, `run_stage4n_*`, `validate_stage4*`, `start_stage4m_simulation.sh`, `emit_stage_gate.py`).

**Complete the uv migration (D8).** Delete `poetry.lock` and the `[tool.poetry]` block from
`backend/pyproject.toml` — but only after verifying a container build and a host bootstrap both succeed from
`uv.lock` alone. If either still needs poetry, the Phase 4 migration is incomplete and this deletion waits.

**Root-level debris — no disposition in the draft plan.** `fix_eslint.py`, `fix_imports.py`, `README-back.md`,
`PACKAGE_MANIFEST.md`, `linux_kit/`, `.tmp/`, `.runtime/`, `backend/validation_output_new.txt`,
`backend/run_live_drift.py`, `backend/run_live_mongo_inventory.py`, `backend/run_live_sql_inventory.py`.
Decide keep/move/delete for each; the `run_live_*` scripts are useful live-stack diagnostics and probably
belong under `backend/scripts/diagnostics/`.

**Documentation.** Remove `docs/evidence/` and obsolete historical runtime docs. **Explicit disposition
required** for `docs/plans/`, `docs/implementation/`, `docs/execution-context/`, `docs/runbooks/`,
`docs/code_quality/`, `docs/review/`, and the 22 root-level `docs/*.md` stage plans — the draft plan named
none of these.

**Retain.** README, architecture, module READMEs, configuration guide, bootstrap guide, operations guide, Vault
guide, AI guide, Graph Schema Analyzer guide, extension guides, and this plan.

**Commit.** `chore(repo): remove obsolete scripts and historical runtime debris`

---

## Phase 28 — Documentation consistency gate

Documentation was updated in every phase. This phase reconciles, it does not author evidence.

**Python.** Module docstring on every production file. Meaningful docstring on every named production
class/function/method covering responsibility, parameters where useful, return contract, side effects,
exceptions where relevant, concurrency, security, and read-only behaviour.

**TypeScript.** Meaningful TSDoc on every exported function, class, hook, service, shared contract, and
non-obvious major component. Named internal production functions follow the same contract-oriented standard.

**Required module READMEs.** `business/`, `agents/`, `configuration/`, `graph/`, `graph_schema_analyzer/`,
`ai/`, `ai/interception/`, `platform/modules/`, `platform/system_store/`, plus one per independent agent.

**Root README final state.** Four product screens, prerequisites, bootstrap, start, stop, infrastructure,
configuration, agents, workflow, graph, AI, extension model, development commands, troubleshooting. No V1/V2
description. **Remove the temporary migration section added in Phase 0.**

Configure existing linters for missing documentation where practical. Do not build a custom documentation
framework.

**Commit.** `docs: align source and README documentation with unified platform`

---

## Phase 29 — Static integrity gate

Full non-behavioral repository sweep.

**Backend.** format/lint, typecheck, `compileall`, import validation, OpenAPI generation, contract generation.
**Frontend.** lint, typecheck, production build, dead import scan.
**Configuration.** every YAML parses; every canonical schema validates; every referenced module ID, agent ID,
workflow handler, AI task/route, and system structure resolves.
**Infrastructure.** `docker compose config`, shell syntax, PowerShell syntax, migration ordering.
**Documentation.** README links resolve, documented commands exist, configuration keys match code, routes match
the application.

Fix every static failure before Phase 30.

**Commit.** `fix: close unified platform static integrity gaps`

---

## Phase 30 — Behavioral validation campaign

*This is the first full behavioral run of the consolidated platform (D7). Phases 1–29 carried static checks,
architecture checks, and the focused invariant tests each phase named — not suites. Budget real time here: this
is where 29 phases of accumulated behavioral debt surfaces and is paid.*

### 30.1 Clean bootstrap
From clean application-owned infrastructure, verify: Vault/Mongo/Neo4j/Valkey/Temporal start; system-store
structures created; system indexes created; SQL migrations applied (**including 003 and 004** — first time
ever); Neo4j migrations applied; baseline configuration activated; backend, workers, and frontend start.

Then restart and verify: existing structures reused; no destructive recreation; no reseeding; no AI validation;
no unnecessary graph rebuild.

### 30.2 Existing-system bootstrap
Start against already-created structures. Verify startup detects, validates, applies only pending migrations,
and continues.

### 30.3 Configuration E2E
Create source → save Vault secret → validate → discover datasets → inspect schema → preview bounded data →
activate → restart → load configuration. **Verify no source mutation occurred** (compare source DDL before and
after).

### 30.4 Graph Schema Analyzer campaign
Against the live MongoDB (`return_source`) and SQL Server (`return_platform`) sources:
- **Discovery** — select source, select dataset, discover metadata, bounded samples, snapshot persistence.
- **AI reasoning** — analyze, ask clarification, receive answer, generate proposal.
- **Edit** — add/remove entity, change relationship, change identifier, change mapping, change index.
- **Validate** — invalid mappings fail; source mutations are impossible.
- **Activation** — approve, build generation N+1, initial sync, catch-up, deep validation, activate, drain N,
  retire N.
- **Failure** — force N+1 validation failure; verify N stays ACTIVE and the candidate is marked FAILED.
- **Drift** — modify a development source schema; verify drift detected, new analysis, new draft, new
  generation, and **no destructive change to the active graph until the replacement is validated**.

### 30.5 AI Control Center campaign
- **Gateway** — provider routing, model selection, fallback, retry, timeout, rate limiting, concurrency,
  circuit breaker.
- **Safety** — prompt injection, business scope, response contract, hallucination guard.
- **Logs/metrics** — trace persistence with provider, model, task, agent, tokens, latency, retry, fallback,
  failure.
- **Interception** — intercept request, create PENDING, claim, manual response, validate, resume.
- **Concurrency** — two operators attempt claim; exactly one wins.
- **Replay** — same route and alternate route.
- **Assisted response** — operator instruction → AI candidate → operator edit → validation → submit.
- **Provenance** — the human response is recorded and displayed as `HUMAN_INTERCEPTION`, never as external
  provider output.

### 30.6 Independent-agent validation
Per agent: unique task queue, unique state namespace, own configuration, own prompt, own AI route, typed
input/output, no direct agent dependency. A static dependency scan confirms no agent imports another agent
implementation.

**Extension proof.** Add one test-only plugin `TEST_AGENT` through the normal plugin mechanism. Verify **zero**
changes required to `main.py`, existing agents, AI Gateway, or Graph Schema Analyzer. This proves the
extension architecture rather than documenting it.

### 30.7 Return Business Copilot E2E
Full lifecycle: Order Discovery → Order Analysis → Return Decision → Support when required → RMA/RGA →
Fulfillment → Label/Tracking/BOL → Physical Return → Warehouse/Bay → Resolution → Feedback Learning.

Cases: standard happy path, ambiguous customer, ambiguous order, no graph result, on-demand sync, AI failure,
AI failover, AI interception, human support, parcel, LTL/freight, warehouse, cancellation, restart/resume,
duplicate requests, optimistic concurrency conflict, integration retry.

### 30.8 Distributed correctness invariants under real infrastructure

The §13 invariant tests were written with their mechanisms. This step re-runs them against the live stack and
adds the multi-process cases that fakes cannot exercise:

- **Fenced lease (13.7).** Start two backend instances against one Mongo, force a migration slower than the
  lease TTL, and confirm exactly one applies it and the other aborts. Then pause the holder past its TTL and
  confirm its subsequent writes are rejected by the token guard, not merely logged.
- **Atomic activation (13.8).** Fire concurrent activations of two different APPROVED releases from two
  replicas; exactly one wins, the loser gets 409, and exactly one release is ACTIVE afterwards.
- **Configuration adoption (13.2).** Activate a release; confirm every replica adopts it, or that a replica
  requiring restart reports pending and degrades `/health/ready` rather than silently running stale config.
  Confirm an in-flight session stays pinned to its start release.
- **Session lease drain (13.3).** Open a `PIN_STRICT` session on generation N, activate N+1, confirm N stays
  `DRAINING` past the drain timeout and the alert names the blocking session. Then close the session and
  confirm N retires. Separately, confirm a `REBIND_ON_RESUME` session resumes on N+1 and revalidates.
- **Generation handle (13.4).** Trigger an on-demand sync and flip the active generation mid-flight; confirm
  the operation restarts with a fresh handle and never writes across two generations.
- **Resume atomicity (13.5).** Kill the backend between interception completion and resume delivery; confirm
  the resume worker delivers on restart and the workflow proceeds exactly once. Force a redelivery and confirm
  the workflow deduplicates.
- **Data classification (13.6).** Dump `ai_interceptions`, `ai_traces`, and `source_snapshots` after a full
  E2E run and grep for known secret values, connection strings, and seeded customer PII. Zero hits required.

### 30.9 Durable reasoning restart validation

**Order Discovery.**
```
start reasoning → complete several nodes → kill the backend → restart
→ the same thread resumes → no duplicate AI call, no duplicate targeted sync → correct result
```

**Graph Schema Analyzer.**
```
start analysis → reach a clarification interrupt → kill the backend → restart
→ answer the clarification → the same analysis thread resumes → proposal remains consistent
```

Also verify: a Temporal activity retry reuses the same `thread_id` rather than opening a new thread; an
`InterceptionPending` inside a reasoning node releases the worker and resumes correctly after the operator
responds; a mid-reasoning generation activation restarts the run cleanly on the same thread with a new
`reasoning_run_id`; and every budget in `reasoning.yaml` terminates its loop rather than running away.

### 30.10 Resilience
Independently restart backend, Temporal worker, orchestrator, outbox publisher, MongoDB, Neo4j, and Valkey.
Verify durable continuation. Graph requests stay generation-consistent throughout activation and draining.

### 30.11 Final gate
Full backend suite, full frontend suite, full integration suite, full E2E, clean bootstrap, existing-state
bootstrap, restart/resilience. No new architecture work after this point unless testing identifies a genuine
architectural defect.

**Commit.** `fix: close unified platform functional validation gaps`

---

## 8. Commit sequence

```
01  chore: establish unified platform consolidation baseline
02  refactor(platform): add neutral contracts and capability kernel        [1A]
03  refactor(platform): add epoch-aware module lifecycle                   [1B]
04  fix(platform): close epoch admission race, full-abort, fatal-commit,
    and dependency-ordering gaps                                          [1B, post-review]
05  fix(platform): close concurrent reconfiguration and epoch cleanup
    races                                                                 [1B, post-review]
06  fix(platform): track epoch holders by unique lease identity, not a
    bare count                                                            [1B, post-review]
07  refactor(config): introduce canonical runtime configuration model
08  fix(configuration): complete Phase 2 release lifecycle, semantic
    validation, and compatibility translation                             [Phase 2, post-review]
09  fix(configuration): fix transaction await bug and target correct
    collection in activation test                                         [Phase 2, post-review]
10  feat(platform): add configuration-driven system store bootstrap
11  refactor(bootstrap): decouple application startup from test tooling
12  refactor(agents): standardize independent agent plugin contract
13  feat(platform): add LangGraph durable reasoning foundation
14  refactor(workflow): make return orchestration agent-independent and config-driven
15  refactor(order-discovery): consolidate on graph-first agent with durable reasoning
16  refactor(sources): unify read-only source connector framework
17  feat(graph-schema): add independent persistent analyzer module
18  feat(graph-schema): implement source-driven schema reasoning
19  feat(graph-schema): add interactive mutation and validation lifecycle
20  feat(graph): complete safe generation activation and draining
21  refactor(ai): consolidate gateway into AI Control Center backend
22  feat(ai): replace manual file provider with durable interception service
23  refactor(config): consolidate configuration control plane
24  refactor(returns): consolidate full return lifecycle backend
25  feat(frontend): add unified four-domain application shell
26  feat(frontend): build end-to-end Return Business Copilot
27  feat(frontend): consolidate platform Configuration experience
28  feat(frontend): rebuild independent Graph Schema Analyzer
29  feat(frontend): build AI Control Center and intervention console
30  refactor(runtime): cut over Data Console APIs to canonical modules
31  refactor(runtime): retire V2 platform shell
32  refactor(runtime): reduce main.py to module activation
33  refactor(frontend): remove legacy V1 V2 and studio surfaces
34  refactor(cleanup): remove superseded platform implementations
35  refactor(bootstrap): finalize production compose topology
36  refactor(config): remove obsolete legacy configuration
37  chore(repo): remove obsolete scripts and historical runtime debris
38  docs: align source and README documentation with unified platform
39  fix: close unified platform static integrity gaps
40  fix: close unified platform functional validation gaps
```

**Policy.** After each phase: code complete → documentation complete → phase gate green → `git diff` reviewed →
commit → push. Never accumulate the refactor into one final commit. No ZIP files, no evidence bundles.

**Commits 02 through 06 are one architectural phase.** They exist as separate commits only to shrink the
review and rollback surface of the most foundational work in the plan — 04, 05, and 06 are three successive
post-review correctness passes on 03's mechanism (04: epoch admission atomicity, full-abort semantics,
fatal-commit fail-closing, dependency-ordered initialization; 05: reconfigure serialization plus stale-epoch
fencing, atomic admission-closed state, retryable release finalization, and shutting down a failing
initializer; 06: unique-identity lease tracking, replacing a bare holder count that was only accidentally
idempotent), all found before Phase 2 began. 1B (03 + 04 + 05 + 06) must immediately follow 1A — do not begin
Phase 2 before 06 lands, and do not treat 1A or any intermediate cut of 1B as a releasable state. The
`ModuleRuntime` contract and epoch model this phase delivers are what Phase 2's configuration adoption is
built on.

---

## 9. Risk register

| # | Risk | Mitigation | Phase |
|---|---|---|---|
| R1 | Behavioral debt accumulates until Phase 30 | Accepted trade-off (D7). Bounded by: test deletion in the same commit as code deletion, architecture tests added with the module they guard, §13 invariant tests written with their mechanism, and import validation in every gate | all |
| R11 | Modules become circularly coupled | Capability registry + four-pass activation; `test_no_module_cross_imports.py`, `test_layering.py` | 1 |
| R12 | UI reports ACTIVE while replicas run stale configuration | Reconciler + `configuration_adoption` + degraded readiness | 2 |
| R13 | Concurrent activation yields two ACTIVE releases | Transactional activation + partial unique index | 2 |
| R14 | Concurrent migrations from an expired lock | Fenced lease + heartbeat + token-guarded writes | 3 |
| R15 | Resumed session bound to a retired generation | Durable session leases; drain gates on all three lease kinds | 12 |
| R16 | On-demand sync writes to a different generation than the query | One `GenerationHandle` per operation, threaded explicitly | 12 |
| R17 | Lost interception → permanently stuck workflow | Embedded resume command; at-least-once worker; idempotent signal | 14 |
| R18 | Credentials or customer data persisted in AI/analyzer records | Mandatory redactor; encrypted structures with TTL; IDs not snapshots | 3, 9, 14 |
| R19 | Container not reproducible against any lockfile | Single uv lock across Docker, host scripts, CI | 4, 27 |
| R20 | LangGraph becomes a second platform orchestrator | D11 boundary; no LangGraph type in any public signature; `test_langgraph_not_in_public_api.py`, `test_no_cross_agent_subgraphs.py` | 5A, 7, 10 |
| R21 | A reasoning node bypasses the AI Gateway | Provider integration packages excluded at the dependency level; `test_no_langchain_provider_packages.py`, `test_nodes_do_not_construct_ai_providers.py` | 5A |
| R22 | Resumed node repeats a side effect (LangGraph re-runs nodes from their start) | Action receipts keyed on `reasoning_run_id + node_name + logical_action_id` | 5A, 7 |
| R23 | A Temporal worker blocks waiting for a human | Both suspension causes return from the activity and resume on the same thread (design §14.8) | 7, 10 |
| R24 | Secrets or customer records land in checkpoints | Allowlist that rejects rather than strips; encrypted structures with TTL | 5A |
| R25 | Unbounded reasoning loop | Validated per-component budgets; `ReasoningLimitExceeded` / `NEEDS_HUMAN_REVIEW` | 5A, 7, 10 |
| R26 | Platform contracts name domain types, re-coupling the kernel | Neutral protocols in `platform/contracts/`; `test_layering.py` | 1 |
| R27 | One capability cannot serve two consumer shapes | Registrations keyed on `(capability, contract)`; match verified at publication | 1 |
| R28 | Replica runs mixed configuration after a partial reconfigure | Two-phase prepare/commit/abort; `test_late_restart_required_aborts_all.py` | 1, 2 |
| R29 | Previous turn's reasoning state contaminates a new turn | Per-turn thread `…:<turn_id>:<attempt>`; explicit resume-vs-new-turn routing | 5A, 7 |
| R30 | Run abandoned while external work still pending | Sweeper preconditions; forced abandonment via Mongo transaction + durable resume command; late completions rejected | 5A |
| R31 | Pinned session silently reads a newer release | `RuntimeConfigurationView` is the only readable object; agents never hold a handle | 1, 2, 5 |
| R32 | Request observes modules on two different releases mid-adoption | One replica epoch-pointer swap; per-request epoch capture; drain before release | 1, 2 |
| R36 | Epoch capture and lease acquisition race, releasing resources a request still holds | `EpochAdmission` — one lock owns the pointer, the CURRENT/DRAINING/RELEASED state, and holder counts; `acquire_current()` is the only admission entry point | 1 |
| R37 | Abort only reaches modules that prepared before the refusing/failing one | `_abort_all` calls every module unconditionally, best-effort | 1 |
| R38 | A caller catches a commit failure and keeps serving on inconsistent module state | `ReplicaStatus.UNAVAILABLE` + `FatalReconfigurationError`, refused on every later call | 1 |
| R39 | A module's `initialize()` runs before a module it depends on has finished | `initialization_dependencies` + `topological_order()` (DFS, cycle/self-dependency/missing-dependency detection) | 1 |
| R40 | A failed `initialize()` leaks already-initialized modules | `initialize_all` shuts down prior modules in reverse order before re-raising | 1 |
| R41 | Kernel built but never exercised alongside real startup | Zero-module `ModuleRegistry` wired into `main.py`'s existing `lifespan()`, proven to execute end to end | 1 |
| R42 | Two concurrent `reconfigure()` calls interleave and an older one regresses the current epoch | `asyncio.Lock` serializes `reconfigure()`; `begin_swap` fences on expected-current and strictly-newer as defense in depth | 1 |
| R43 | A request is admitted after the replica is already fatal-UNAVAILABLE | Accepting-flag lives inside `EpochAdmission`'s own lock, checked atomically with holder registration | 1 |
| R44 | A module cleanup failure during release permanently strands an epoch as falsely `RELEASED` | Intermediate `RELEASING` state; `finish_release` only fires once every module's cleanup actually succeeds; retryable | 1 |
| R45 | A module whose own `initialize()` fails leaks its partially-created resources | `initialize_all` shuts down the failing module itself, not just the ones before it | 1 |
| R33 | Crash between abandonment commit and Temporal signal strands a workflow | `reasoning_resume_commands` outbox, at-least-once delivery, idempotent on `command_id` | 5A |
| R34 | Stale lock holder cannot be fenced | `bootstrap_locks` persists `lease_id` + `fencing_token`; all writes CAS on the triple | 3 |
| R35 | Adapter passes `isinstance` but has a wrong signature | Three-layer conformance: publication check + typed factory checked by mypy + contract test | 1 |
| R2 | Router cutover too large to revert | Split into 22a/22b/22c, each independently revertible | 22 |
| R3 | Committed OpenAPI artifacts drift from code | Regenerate and commit in the same commit as any contract change | 9,11,13,14,15,16,22 |
| R4 | `config/v2` deleted before promotion completes | D1 — promote in P2, verify before removing the husk in P26 | 2, 26 |
| R5 | SQL 003/004 apply for the first time in production bootstrap | Apply against the live stack in P4 and verify objects; do not defer to P30 | 4 |
| R6 | Generation activation unsafe without leases | P12b is mandatory before any traffic resolves `ActiveRuntimeSnapshot` | 12 |
| R7 | RBAC assumed but absent | Built in P17 before the screens that depend on it | 17 |
| R8 | Fakes hide physical-vs-logical field bugs | Live-stack rule (§5) on every source/graph/sync phase | 7,8,10,12 |
| R9 | Third Neo4j writer deleted or kept by accident | Explicit investigation item in P24 | 24 |
| R10 | Plan collides with the in-flight source-to-graph plan | D5 — this document is authoritative from the branch point | — |

---

## 10. Capabilities that must survive

Temporal return orchestration; return sessions; support/RMA; fulfillment; physical operations; warehouse/bay;
feedback; Dynamic Order Discovery; `ActiveSchema`; generic MongoDB connector; generic SQL Server connector;
dynamic projection; generic full sync; incremental sync; on-demand sync; graph generation fencing; generation
activation/draining; AI Gateway; AI provider/model routing; fallback; rate limiting; circuit breakers; AI logs;
metrics; safety; interception; replay; Vault; Platform MongoDB; Neo4j; Valkey; Temporal PostgreSQL; integration
outbox; SQL migrations; Neo4j migrations; bootstrap scripts; runtime configuration; audit; RBAC.

**Note on Order Discovery.** The *capability* survives in full — progressive search, conversation memory,
strong-anchor validation, hallucination and response guards, on-demand sync. What changes is the internal
structure: `DynamicOrderAgentCoordinator`'s procedural state machine decomposes into bounded LangGraph nodes
with durable checkpoints (D11). Behaviour is preserved; the coordinator class is not.

---

## 11. Acceptance criteria

**Product.** Exactly four main screens: Return Business Copilot, Configuration, Graph Schema Analyzer, AI
Control Center.

**Architecture.** No user-visible or architectural V1/V2 split. All eight distributed correctness invariants
in design §13 are implemented and their named tests pass.

**Agents.** All independently registered and configured. No agent directly invokes another agent, proven by
static scan. Exactly six business agents — the Graph Schema Analyzer is not one of them.

**Durable reasoning.** LangGraph powers Order Discovery reasoning and Graph Schema Analyzer reasoning, and no
other component. Temporal remains the only cross-agent/business orchestrator. All AI calls still pass through
the AI Gateway; all source and graph access still passes through module ports. Reasoning state and pending
user clarifications survive a backend restart. Every reasoning loop is bounded, every node side effect is
idempotent, checkpoint storage is configuration-driven, encrypted, and retention-controlled, and no secrets or
raw source records are persisted in reasoning state. Order Discovery stays graph-generation consistent.
Analyzer reasoning can neither activate a graph generation nor modify a source system. Removing LangGraph from
either component requires no change to Temporal, the AI Gateway, other agents, or the four product screens.

**Extension.** Adding an agent, source connector, AI provider, or module requires no change across unrelated
modules — proven by the `TEST_AGENT` exercise in 30.6.

**Configuration.** Runtime behaviour is configuration driven. Secrets are in Vault.

**Sources.** No hardcoded business table/collection/field assumptions. External sources remain read-only,
verified by DDL comparison.

**System store.** Missing configured structures are created at startup; existing valid structures are reused;
no destructive normal-start recreation.

**Graph.** The Analyzer is independent and functional. Schema is editable and conversational. Validation runs
against configured sources. Indexes and constraints are graph-side only. Generation cutover is safe, leased,
and drained.

**AI.** Logs, metrics, configuration, and status are visible. Durable manual interception works. Exact and
alternate replay work. Operator-assisted generation works. Human output is attributed as `HUMAN_INTERCEPTION`.

**Business.** The return flow operates from Order Discovery through Feedback Learning.

**UI.** No Data Studio, AI Studio, or simulator application.

**Documentation.** Every production file, class, and function is documented. Every independent module has a
current README. The root README describes the actual current application and startup process.

**Bootstrap.** A clean checkout can be configured and started using the documented procedure. A normal restart
does not reseed, recreate valid system data, rebuild the graph, or perform unnecessary live AI validation.

**Quality.** Full backend, frontend, integration, bootstrap, resilience, and E2E validation pass.
