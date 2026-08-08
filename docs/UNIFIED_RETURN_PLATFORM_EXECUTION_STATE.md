# Execution state

Branch: `refactor/unified-return-platform`
Verified HEAD: pending this slice's commit (previous: `f5e5591`)
Last pushed green commit: `f5e5591` (`refactor(workflow): make return orchestration agent-independent and config-driven`)
Slice: **Phase 8 / Wave C1 — Canonical read-only source connector framework**
Status: DONE

## Phase 6 / Wave B3 — Temporal return orchestration

Status: DONE (see git history for `f5e5591`; superseded as "current slice" by Phase 8 below).

## Phase 5A / Wave B2 — LangGraph durable reasoning foundation

Status: DONE (see git history for `2b86e4c`; superseded as "current slice" by Phase 6 below).

## Phase 5 / Wave B1 — Independent agent plugin contract

Status: DONE (see git history for `c3e7fc2`; superseded as "current slice" by Phase 5A above).

## Phase 4 — Bootstrap correctness

Status: DONE (see git history for `b19570f`; superseded as "current slice" by Phase 5 above).

## Slice 3R — SystemStore correctness hardening

Status: DONE (see git history for `65106ce`; superseded as "current slice" by Phase 4 below).

## Verified current facts

- `uv run` is unsafe for gates in this repo today: `pyproject.toml` uses Poetry-style
  dependency groups (`[tool.poetry.group.dev.dependencies]`) that `uv sync` does not
  read; a prior `uv sync --all-groups` uninstalled pytest/ruff/mypy. Both `uv.lock` and
  `poetry.lock` exist. Dependency-manager unification is Phase 4's job, not done yet.
  **Gates in this slice use Poetry** (`poetry run ...`), matching every gate run so far
  this session.
- Mongo-dependent tests must run from a runtime that resolves the compose network's
  service hostnames (`mongodb:27017`), not by rewriting the replica set's advertised
  host to `127.0.0.1` (a prior session did this; it was reverted — replica set host is
  back to `mongodb:27017`). Test runner: a throwaway `python:3.13-slim` container named
  `slice3r-test-runner`, attached to docker network `return-multi-agent-platform_platform`,
  with `backend/` and `.env` bind-mounted, Poetry installed inside it. Removed at the end
  of the slice.
- A non-transactional write (e.g. a heartbeat) to a document currently held by an open
  MongoDB transaction *blocks* behind that transaction's document lock rather than
  producing a write conflict (confirmed empirically). Two concurrent *transactions*
  both writing the same document CAN produce a genuine `WriteConflict`/
  `TransientTransactionError`, but reliably forcing that race live (without it
  degenerating into a livelock when retries resynchronize every round) proved highly
  timing-sensitive. `test_fenced_writes_reject_stale_token.py`'s transient-vs-fatal
  retry classification is proven by driving the real `OperationFailure`/label mechanism
  directly instead.
- **Incident**: the container's Poetry was initially left at `virtualenvs.in-project=true`,
  which wrote a Linux-executable venv into the bind-mounted `backend/.venv` — the same
  directory as the host's Windows venv — corrupting it. Fixed by setting
  `virtualenvs.in-project=false` + `virtualenvs.path=/root/.venvs` inside the container
  (venv lives outside the bind mount) and reinstalling the host venv with
  `poetry install --sync`. Lesson: any container that bind-mounts `backend/` must keep
  its own venv outside that mount.
- Collection names already match the target design's §13 schema table:
  `platform_bootstrap_locks`, `platform_fencing_tokens`, `platform_schema_versions`.
- mypy strict baseline on `src` before this slice: 44 errors in 14 files (unchanged since
  the a379762 commit).
- Full backend suite baseline before this slice: 1569 passed, 3 skipped (excluding the
  pre-existing, unrelated `test_order_agent_rest.py` Vault-dependent failures).

## Canonical ownership changes (this slice)

- `platform/system_store/mongo.py` — fencing guard becomes a real conditional mutation
  (`FencedMongoTransactionGuard.assert_and_lock`/`verify_fence`), transient-vs-fatal
  retry split, index gateway returns full spec, version ledger gains physical-identity
  binding, new `MongoBootstrapStateStore`.
- `platform/system_store/contracts.py` — `StructureIdentity`, `IndexDefinition`,
  `IndexDriftReport`/`IndexEnsureResult`, `BootstrapState`/`BootstrapStatus`,
  `compute_structure_fingerprint`/`compute_manifest_fingerprint`.
- `platform/system_store/migrations.py` — new `MigrationPathValidator`
  (`MigrationDowngradeUnsupported`/`MigrationPathInvalid`).
- `platform/system_store/locking.py` — `bounded_retry_with_jitter` (shared by 3R.1's
  transaction retry and 3R.8's bootstrap waiter).
- `platform/system_store/bootstrap.py` — winner/waiter/takeover flow,
  `SystemStoreBootstrapTimeout`/`IndexDriftDetected`/`StructureVanishedDuringBootstrap`.
- `platform/system_store/repository.py`, `encryption.py` — restricted `read_only()`
  facade (`_ReadOnlyCollectionView`, genuinely no mutation methods),
  `EncryptedStructureRequiresGuardedAccess`, envelope shape validation, allowlisted
  top-level fields for encrypted structures.
- `platform/secrets/envelope.py` — `EnvelopePayload` fields renamed/extended to
  `ciphertext`/`key_ref`/`algorithm`/`version`.
- `configuration/application/snapshot.py` — new shared `verify_snapshot_integrity()`.
- `configuration/application/release_service.py`, `activation.py`,
  `runtime_configuration.py` — all three route through `verify_snapshot_integrity`;
  `approve_release()`/`activate_release()` previously had no integrity check at all.
  Checksum mismatch now raises `ConfigurationIntegrityError`, not
  `InvalidTransitionError`/`ActivationConflictError` (an integrity violation is not an
  ordinary transition conflict).
- `backend/tests/conftest.py` — `test_settings` mongo host is now overridable via
  `PLATFORM_TEST_MONGO_HOST` (defaults to `localhost`), so Mongo-dependent tests can run
  from a container attached to the compose network without rewriting replica-set config.
- New: `scripts/dev/run_changed_gate.py`, `scripts/dev/precommit_guard.py` — both
  exercised against this slice's own diff (a known-good pass and one intentionally
  introduced, then removed, violation each).

## Real bugs found and fixed while hardening (not in the original 9-item list)

- `MongoBootstrapStateStore.read()` crashed with a pydantic `extra_forbidden` error
  because the raw Mongo document's `_id` field isn't declared on `BootstrapState` —
  fixed by stripping `_id` before `model_validate`.
- `verify_snapshot_integrity()`'s new call sites in `activation.py`/`release_service.py`/
  `runtime_configuration.py` initially regressed mypy (+5 errors) because a Mongo
  document field typed `object` was passed where `str` is required — fixed with
  explicit `str(...)` conversions at each call site.
- The two-bootstrap-contenders test's first draft monkeypatched `create_structure` with
  a 2-party barrier, but only the winner ever reaches `create_structure` (the loser is
  diverted to the waiter path at lease acquisition) — the barrier could never be
  satisfied. Removed the artificial delay entirely; the real atomic lease-acquisition
  CAS already proves exactly-one-owner without it.

## Open blockers

(none)

## Gate receipts

- `poetry run mypy src` (backend): 44 errors in 14 files — unchanged from the
  pre-slice baseline (verified via a before/after stash comparison), zero regressions.
- `poetry run ruff check`/`ruff format --check` on every touched file: clean except
  6 pre-existing errors (2 `B904` in `activation.py`, 3 `RUF059` in
  `test_release_lifecycle.py`, none in files/lines this slice touched) — confirmed
  pre-existing via the same baseline-diff technique.
- `poetry run pytest tests/platform tests/configuration` (real Docker Mongo replica
  set, via a throwaway container attached to the compose network): 151 passed, stable
  across 3 consecutive runs.
- `poetry run pytest` full suite, host, excluding Mongo-dependent tests and the
  pre-existing unrelated `test_order_agent_rest.py`: passed (exit 0).
- `python scripts/dev/run_changed_gate.py` and `python scripts/dev/precommit_guard.py`:
  both exercised against this slice's real staged diff; each caught a real issue
  introduced mid-slice (a formatting/trailing-newline regression; an intentionally
  introduced stub-function violation) and passed clean once fixed/reverted.
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.

## Phase 4 — Bootstrap correctness (this slice)

**4a — SQL migration runner.** New `configuration/cli/apply_sql_migrations.py`, modeled exactly
on `apply_neo4j_migrations.py`: discovers packaged `NNN_*.sql` files via `importlib.resources`,
tracks applied migrations by name+checksum in `platform.schema_migrations`, splits files on `GO`
batch separators, connects via `pymssql`. `compose.yaml`'s `sqlserver-init` now only waits for the
`return_platform` database to come online (no longer runs `sqlcmd -i`); `runtime-configuration-init`
runs `apply_sql_migrations.py` after the Neo4j migration step and depends on `sqlserver-init`.

**Real bug found while verifying against live SQL Server (003/004 had never actually been
executed until this slice — they were dead files referenced only from docs):** three blocks in
`003_production_return_platform.sql` did `ALTER TABLE ... ADD <col>` and then referenced that
same column in an `UPDATE`/`ALTER COLUMN` **in the same batch**. SQL Server resolves DML column
references against the schema snapshot at batch-*compile* time, not incrementally as DDL executes,
so this fails with `Invalid column name` — confirmed empirically against the live container
(`ALTER TABLE ... ADD x; UPDATE ... SET x = ...;` in one batch reliably fails; DDL-to-DDL chains
in one batch, e.g. `ADD` then `ALTER COLUMN` then `ADD CONSTRAINT`, do not have this problem).
Fixed by splitting each ADD from any DML referencing the new column into its own `GO` batch, with
`WHERE col IS NULL`-guarded backfills so re-invocation stays a no-op. This was never caught before
because these two files were never actually run.

**4b — Decouple `backend`/workers from `seed-runner`.** `backend`, `return-workflow-worker`, and
`return-orchestrator` no longer depend on `seed-runner`; they now depend directly on
`runtime-configuration-init` + `mongodb-rs-init` (+ `temporal`/`valkey` as before). `seed-runner`
itself is unchanged and still runs, but no longer blocks application startup (Phase 25's job is to
move it to a `dev-tools` profile).

**4c — uv dependency unification.** Added a PEP 735 `[dependency-groups]` table to
`backend/pyproject.toml` mirroring `[tool.poetry.group.dev.dependencies]`, so
`uv sync --all-groups` resolves pytest/ruff/mypy/etc. without the old hardcoded
`uv pip install ...` fallback line. `backend/Dockerfile` now builds from `uv.lock`: a pinned
`ghcr.io/astral-sh/uv:0.11.28` stage supplies the `uv` binary, which runs
`uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt` before the
existing `pip wheel -r requirements.txt .` step — chosen over `uv sync --no-dev` directly in the
image so the existing two-stage wheel-based build (and its non-root final image) didn't need to
change shape. `scripts/bootstrap_host.sh` gained a uv-first branch (previously Poetry-only, unlike
the `.ps1` counterpart which already had one); `scripts/linux/02_reconstruct_environment.sh` and
`scripts/linux/redeploy_app.sh` gained uv-first branches with a Poetry fallback.
`scripts/bootstrap_host.ps1`'s existing uv branch had its now-redundant separate
`uv pip install pytest==... mypy==...` line removed (covered by `--all-groups` once
`[dependency-groups]` existed). `poetry.lock`/`[tool.poetry]` are kept per the plan (D8) until
Phase 27.

## Real-infra gate (Phase 4)

- Clean SQL bootstrap: `sqlserver` + `sqlserver-init` + `runtime-configuration-init` (SQL step
  only, isolated from the unrelated Neo4j blocker below) against a real Dockerized SQL Server —
  `applied=001_return_business_state.sql` / `002` (already applied in an earlier session, so
  `skipped=`) / `applied=003_production_return_platform.sql` / `applied=004_production_bay_constraints.sql`
  / `sqlserver_schema_status=READY`.
- Restart (idempotency): re-ran the same command — all four `skipped=`, zero errors.
- Verified real objects: `platform.bay_reservation`, `platform.return_policy_version` exist;
  `dbo.return_requests.trilogie_order_number` (64 chars); `platform.bay_configuration.hazardous_allowed`
  (BIT); `CK_bay_type`/`CK_bay_assignment_status` carry the new enum values; `bay_assignment.handling_unit_id`
  is `NOT NULL`; `UQ_bay_assignment_return_handling_unit` and `FK_bay_assignment_reservation` exist.
- Mongo bootstrap: `mongodb` + `mongodb-rs-init` against a real container — replica set init
  succeeded (exit 0).
- Restart application: `backend` built via the new uv-based Dockerfile and started directly
  (`--no-deps`, since `valkey`/`temporal` weren't part of this specific check); `/health/ready`
  reported `mongodb`/`source_mongodb`/`sqlserver`/`neo4j` all `HEALTHY` with no `seed-runner`
  dependency in the path. `valkey`/`temporal` showed `UNAVAILABLE` only because they weren't
  started in this scoped check, not because of anything this slice changed.
- No seed mutation: confirmed via `docker compose --profile containerized-app config` — `backend`/
  `return-workflow-worker`/`return-orchestrator` no longer reference `seed-runner` in `depends_on`.
- No AI validation: `PLATFORM_VALIDATE_AI_ON_STARTUP` still defaults to `false`; unchanged by this
  slice.
- `docker compose --profile containerized-app config --quiet`: valid, no broken references from
  the dependency-graph edits.
- `uv sync --all-groups --frozen` into an isolated venv: resolves pytest/ruff/mypy/pydantic/the
  local package; `ruff check src` on that venv produced results byte-identical to the same command
  run through the existing Poetry venv (180 pre-existing errors, unrelated to this slice, both
  runs agree line-for-line) — proves toolchain parity. (`mypy` failed to import its compiled
  extension only inside that specific test venv because the scratch path was 257 characters,
  over a Windows DLL-loader limit; confirmed unrelated to uv/dependency-groups by running
  `mypy --version` successfully through the real, shorter-pathed Poetry venv at 237 characters —
  the real `backend/.venv` path is ~155 characters, well clear of the limit.)
- `docker compose --profile containerized-app build backend` (from a real, uv-lock-derived
  `requirements.txt`) then `docker run ... python -c "import return_platform.main"`: clean.

## Known pre-existing blocker (not introduced by this slice, flagged separately)

The persistent local `neo4j_data` Docker volume has widespread duplicate `Customer` nodes
(same `customer_key`, e.g. `CUST-1657`..`CUST-1676`+), almost certainly from `seed-runner` having
been run more than once against the same volume in an earlier session. This blocks the Neo4j
migration that adds a `uq_customer_key` uniqueness constraint from ever succeeding again on this
volume (`Neo.DatabaseError.Schema.ConstraintCreationFailed`), which in turn blocks
`runtime-configuration-init`'s Neo4j step (and therefore `backend`'s normal `depends_on` chain)
end-to-end on this specific machine's Docker volumes. Not touched here — deduping graph nodes
safely (without silently dropping relationships) is its own task, flagged out-of-band rather than
fixed inline.

## Phase 5 / Wave B1 — Independent agent plugin contract (this slice)

**Scope.** "All agents become replaceable plugins." Migrated the existing six agents
(Order Discovery, Order Analysis, Return Workflow, Return Fulfillment, Bay Assignment,
Feedback Learning) onto one canonical `AgentPlugin` contract and merged the two
registries a prior review found (`agents.registry.ReturnAgentRegistry` — no
configuration metadata; `dynamic_knowledge.agents.registry.IndependentAgentRegistry` —
descriptor-only, confirmed completely unused, zero call sites) into one
`agents.registry.AgentRegistry`.

**New platform primitives, built because `AgentExecutionContext` needs them and they
didn't exist yet:** `platform/contracts/audit.py` (`AuditEvent`/`AuditSink`) +
`platform/audit/logging_sink.py` (`LoggingAuditSink`, a real structured-log
implementation, not a stub); `platform/contracts/redaction.py` (`Redactor`) +
`platform/redaction/allowlist.py` (`AllowlistRedactor`, fail-closed by construction).
Deliberately minimal — a durable SystemStore-backed audit store is a separate future
concern, introduced as a second `AuditSink` implementation behind the same contract.

**`agents/contracts.py` (flat module) became `agents/contracts/` (package):** existing
DTOs moved unchanged to `dto.py`; added `descriptor.py` (`AgentDescriptor` — merges the
two prior descriptors' fields: agent_id, implementation_id, task_queue,
state_namespace, prompt_ref, policy_ref, ai_route_ref, enabled, timeout, retry, rate/
concurrency, circuit breaker), `context.py` (`AgentExecutionContext` — platform-neutral
by construction: configuration/capabilities/audit/redactor/principal/correlation_id/
session_id/configuration_release_id/clock/consistency, no `.ai`, no `.knowledge`, no
other agent), `plugin.py` (`AgentPlugin[RequestT, ResultT]` — generic over each agent's
own typed request/response pair rather than one shared envelope), `ports.py`
(`AgentAiPort`/`KnowledgePort` — declared, not yet bound to a provider). The package's
`__init__.py` re-exports everything, so no caller's import statement needed to change.

**Six agents migrated, existing `assess()`/`analyze()`/`disambiguate()` methods kept
completely unchanged** (per the plan's "adapt, do not rewrite working logic"): each
gained a `descriptor` property and an `async execute(request, context)` adapter. Five
of six are trivial (`del context; return self.assess(request)`). The sixth,
`OrderAnalysisAgent`, is the only agent that calls AI, and its `analyze()` needs a
concrete `AIGatewayService` — `AgentExecutionContext` deliberately has no `.ai` field,
and no adapter under `bootstrap/adapters/` publishes `CapabilityName.AI_INVOCATION` for
agents yet, so its `execute()` raises `NotImplementedError` with a message pointing
callers at `analyze()`/`disambiguate()` (which is what every real caller already uses).
This is a conscious, documented boundary, not an oversight — flagged and reviewed
because `scripts/dev/precommit_guard.py`'s placeholder scan correctly caught it.

**Config extended, not replaced.** `AgentConfiguration`
(`configuration/return_configuration.py`) gained `implementation_id`/`task_queue`/
`state_namespace` (required) and `prompt_ref`/`policy_ref`/`ai_route_ref`/timeout/
retry/rate-limit/circuit-breaker fields (optional, defaulted). `config/returns/
production.yaml` populated with real values for all six agents —
`ai_route_ref` set ONLY where a real, dedicated, already-wired AI Gateway route
exists (`order_analysis`: `ORDER_CANDIDATE_ANALYSIS_V1`; `feedback_learning`:
`FEEDBACK_RECOMMENDATION_V1`, configured in `ai_gateway.yaml` but not yet called by
that agent's code — declaring where AI-assisted recommendations will plug in without
fabricating a call that doesn't happen). `order_discovery`/`return_workflow` are
marked `ai_assisted: true` in config but call no AI today (pre-existing, aspirational
flag, not touched) and correctly have no `ai_route_ref`.

**Four call sites updated:** `api/return_agents.py`, `operations/associate_flow.py`
(import + class rename only — `ReturnAgentRegistry` → `AgentRegistry`, same `.build()`
classmethod, same attribute access); `operations/production_workflow.py`,
`operations/warehouse/service.py` (previously constructed `ReturnFulfillmentAgent`/
`FeedbackLearningAgent`/`BayAssignmentAgent` directly, bypassing any registry — now
route through `AgentRegistry.build(configuration).<attr>`, so there is exactly one
place agents are constructed).

**Deliberately not done this slice (documented scope boundaries, not gaps):**
- `config/agents/*.yaml` (8 module-descriptor YAMLs with a very similar-looking but
  differently-shaped schema) stay unwired — confirmed zero code references them; the
  matching `AgentConfigNode`/`AgentsConfig` model in `configuration/domain/agents.py`
  is equally unused. Reconciling two parallel, never-finished config systems is a
  separate, larger judgment call than "add the fields this phase's contract needs."
- No real capability publication (`bootstrap/adapters/agent_ai_adapter.py` or similar)
  binds `AgentAiPort`/`KnowledgePort` to a provider — Phase 5 declares the shape
  agents resolve; a later phase binds it.
- Wave B2 (LangGraph foundation) and B3 (Temporal orchestration) are separate slices
  per the plan's own wave structure ("B1/B2 analysis can run in parallel; B3
  integrates after both").

## Real bugs found and fixed while hardening (not part of the original ask)

- `tests/test_stage4_schema_and_seed_contracts.py::test_sql_registry_tables_are_created_by_versioned_migrations`
  was failing on a full-suite run — a real regression from **Phase 4**, not this
  slice: the SQL migration files it reads moved to
  `backend/src/return_platform/configuration/sql_migrations/` in that phase, but this
  test still pointed at the old, now-empty `infra/sqlserver/init/`. Never caught
  because Phase 4's own gate only ran targeted tests, not the full suite. Fixed by
  updating the path; confirmed no other stale references exist.
- `scripts/dev/run_changed_gate.py` (built in Slice 3R) had two real bugs, both
  surfaced for the first time by this slice's diff shape: (1) it passed deleted file
  paths to `ruff format`/`ruff check`, which can't open a file that no longer exists —
  never triggered before because no prior slice deleted a tracked `.py` file; fixed by
  filtering `backend_py` to files that still exist on disk. (2) the `openapi-drift`
  gate's command selection was simply wrong — it ran `frontend/scripts/check-bundle.js`
  (which checks a frontend build's `dist/` output for leaked mock-service-worker
  artifacts, unrelated to OpenAPI drift) whenever that file happened to exist, instead
  of the actual drift checker; and even after pointing it at the correct
  `scripts/check_openapi_drift.py`, it ran under bare `python` instead of the backend's
  Poetry environment, so it failed with `ModuleNotFoundError: No module named 'redis'`
  before it could check anything. Fixed by running via `poetry -C backend run` with an
  absolute script path (`-C backend` changes the subprocess's cwd, so a relative path
  broke). Never triggered before because no prior slice touched `agents/contracts.py`'s
  DTOs alongside `api/`, the combination that first activates the `openapi-drift` gate.

## Known pre-existing conditions (confirmed unrelated to this slice)

- `openapi-drift` fails: the committed `openapi/return-platform.openapi.json` snapshot
  has drifted from the live-generated schema. Confirmed via a direct before/after
  check — identical failure (same `openapi_sha256`, same `"DRIFT: root"`) at clean
  HEAD (`b19570f`, before any of this slice's changes) as with this slice applied.
  This slice's only touch to `api/` (`return_agents.py`) is an import/class rename
  with zero schema impact.
- `ruff format --check` on `operations/associate_flow.py` fails: confirmed
  pre-existing via the same before/after technique (the file was already
  non-compliant with the current formatter at clean HEAD). This slice's only touch to
  that file is a two-line import/attribute rename; reformatting the surrounding
  ~2900 lines was out of scope and would have buried the real diff.
- `mypy src`: 44 errors in 14 files — unchanged from the Phase 4 baseline, verified via
  direct comparison. This slice's new/changed files (`agents/`, `platform/audit/`,
  `platform/redaction/`, `configuration/return_configuration.py`) are 100% clean.
- 29 test ERRORs (`test_ai_route_balancing_design.py`, `test_configuration_api.py`,
  `test_probes.py`, others) all trace to the same cause: `NVIDIA_API_KEY`/
  `GOOGLE_API_KEY` were rotated out of `.env` during this session's earlier security
  fix (`fbfcf05`) and never replaced with real values — the `test_settings` fixture
  requires them. Not fixable here (real keys can't be fabricated); the user needs to
  add rotated key values to `.env`.

## Gate receipts

- `poetry run mypy src`: 44 errors in 14 files — unchanged baseline, zero regressions.
- `poetry run ruff format --check` / `ruff check` on every new/changed file: clean,
  except the one confirmed-pre-existing file noted above.
- `poetry run pytest tests/agents/`: 11 passed (including the two new architecture
  tests, `test_no_cross_agent_imports.py` and `test_context_has_no_module_fields.py`).
- `poetry run pytest -q --ignore=tests/test_order_agent_rest.py --ignore=tests/platform
  --ignore=tests/configuration`: 1420 passed, 3 skipped (up from 1419 after the Phase 4
  regression fix), 29 errors (all the pre-existing NVIDIA/GOOGLE key gap above).
- `tests/platform tests/configuration --collect-only`: 151 tests collect cleanly (same
  count as Slice 3R's baseline) — confirms no import breakage from the new
  `agents/contracts/` package split or the new `platform/audit`/`platform/redaction`
  packages.
- `python scripts/dev/precommit_guard.py`: 3 flags, all on
  `OrderAnalysisAgent.execute()`'s documented `NotImplementedError` (in the method
  itself and its two READMEs) — reviewed and consciously accepted as described above,
  not a false negative the guard missed.
- `python scripts/dev/run_changed_gate.py`: two real bugs found and fixed in the gate
  script itself (see above); after fixing, all gates pass except the three confirmed
  pre-existing conditions.
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.

## Phase 5A / Wave B2 — LangGraph durable reasoning foundation (this slice)

**Scope.** Introduce LangGraph without coupling any business module to it — "nothing
uses it yet"; later phases (Order Discovery's and the Graph Schema Analyzer's own
reasoning graphs) are the first real callers. User explicitly chose to attempt the
full spec (including the abandonment sweeper and Temporal-signal outbox worker) rather
than defer the operationally exotic pieces, after being told upfront that the pattern
referenced for those ("same outbox discipline as §13.5") doesn't exist anywhere in the
codebase yet — confirmed via research before starting.

**Dependencies added:** `langgraph==1.2.10`, `langgraph-checkpoint==4.2.0` (pulls in
`langchain-core`/`langchain-protocol`/`langgraph-prebuilt`/`langgraph-sdk`/`langsmith`
— all core LangGraph abstractions, never a provider integration package). Verified via
`uv.lock`/`pyproject.toml` text scan (`tests/reasoning/test_no_langchain_provider_packages.py`):
no `langchain-openai`/`langchain-anthropic`/`langchain-google*`/`langchain-community`/
`langchain-aws`. A real third-party MongoDB checkpoint saver
(`langgraph-checkpoint-mongodb`) was evaluated and rejected: it requires
`pymongo<4.17`, which would downgrade the already-tested `pymongo==4.17.0` Slice 3R is
built against — `SystemStoreCheckpointSaver` is built from scratch instead, modeled
closely on the official `InMemorySaver` reference implementation's method shapes.
Also added `cryptography==50.0.0`, for a real (if interim) envelope encryptor -- see
below.

**`platform/reasoning/` package, all real, all real-Mongo/real-Temporal tested (never
mocked for a correctness claim):**
- `checkpoint.py` — `SystemStoreCheckpointSaver(BaseCheckpointSaver[str])`, async-only
  (`aget_tuple`/`alist`/`aput`/`aput_writes`/`adelete_thread`/`get_next_version`).
  Checkpoints/writes are naturally insert-only (LangGraph allocates a fresh
  monotonically-increasing checkpoint id per `put()`), so `SystemStore.insert_one()`
  (the one write path permitted on an `encrypted: true` structure) is sufficient,
  except the four special negative `WRITES_IDX_MAP` write indices (ERROR/SCHEDULED/
  INTERRUPT/RESUME), which legitimately overwrite via the new `replace_one(...,
  upsert=True)`. Verified end-to-end against a real `langgraph.graph.StateGraph`: real
  execution, real AES-256-GCM encryption (wrong key genuinely fails decryption), real
  restart-durability (a brand-new saver instance sees a prior instance's checkpoints),
  real deletion.
- `receipts.py` — `ReasoningActionReceipts`, the full idempotency state machine
  (STARTED → COMPLETED | PENDING_EXTERNAL | FAILED_RETRYABLE | FAILED_FINAL;
  PENDING_EXTERNAL → COMPLETED | FAILED_FINAL) against real Mongo, atomic via
  `find_one_and_update`. Proven: a `begin()` mid-PENDING_EXTERNAL never re-runs the
  action or loses `external_ref`; `FAILED_RETRYABLE` gets a genuinely fresh attempt
  number on retry; a terminal receipt refuses any further transition (this is also
  what makes late-completion-after-abandonment rejection work, for free).
- `retention.py` — `CheckpointRetentionPolicy`. Active states (`RUNNING`/
  `INTERRUPTED`/`WAITING`) compute no `expires_at`; terminal states compute
  `terminal_at + terminal_retention_hours` and `mark_terminal()` stamps the identical
  value across the run, its checkpoints, its writes, and its receipts in one Mongo
  transaction. TTL indexes (`expire_after_seconds: 0`) added to
  `reasoning_runs`/`reasoning_action_receipts` in `system_store.yaml`.
- `abandonment.py` — sweeper (idle `INTERRUPTED`/`WAITING` past a threshold →
  `ABANDONED`) gated on five real precondition checks (unresolved clarification
  interrupt via the checkpoint's own `pending_writes`; open `STARTED`/
  `PENDING_EXTERNAL` receipt; open `ai_interceptions` record; pending
  `reasoning_resume_commands`; active Temporal workflow). `ForcedAbandonment.abandon()`
  is one Mongo transaction: run → ABANDONED, open interceptions → CANCELLED, open
  receipts → FAILED_FINAL, `expires_at` stamped everywhere, and a
  `reasoning_resume_commands` PENDING row inserted, together. Verified against real
  Mongo: a clean run abandons; a run with any of the five blockers is reported
  `ABANDONMENT_BLOCKED` with the specific blocking reference and is never mutated; the
  sweeper correctly separates idle-clean/idle-blocked/still-fresh runs.
- `resume_worker.py` — `ReasoningResumeWorker`, lease/claim/backoff shape mirroring
  `operations.integrations.outbox.IntegrationOutboxDispatcher`, delivering
  `reasoning_resume_commands` as real Temporal signals via
  `client.get_workflow_handle(...).signal(...)`. **Verified against a real Temporal
  server and a real (throwaway, test-only) workflow** — not a mock: signal genuinely
  received (confirmed via a real workflow query), workflow-side dedup on `command_id`
  proven by redelivering the same signal and confirming it's processed exactly once, a
  new worker instance recovering a command left by a "crashed" predecessor (row
  inserted directly, no prior delivery attempt), and the no-recipient case
  (`workflow_id is None` → immediately `DELIVERED`, correctly not a failure).
- `redaction.py` — `CheckpointRedactor`, rejects (never silently strips) a checkpoint
  state key outside a declared allowlist.
- `thread_ids.py`, `observability.py`, `errors.py`, `configuration.py` (loads
  `config/reasoning.yaml`) — straightforward, all real, no gaps.

**Two real architectural gaps found and closed, not part of the original ask:**
- TTL indexes were not supported by `IndexDefinition`/`StructureDefinition` at all
  (its own Slice 3R docstring said so explicitly: "adding support... requires
  extending the typed model and config schema first"). Extended `IndexDefinition`
  (contracts.py) and `MongoStructureGateway.create_index`/`ensure_indexes` (mongo.py)
  with `expire_after_seconds`, matching the existing `unique`/`partial_filter_expression`
  pattern exactly. Verified: all 18 pre-existing `tests/platform/test_index_drift.py`/
  `test_system_store_bootstrap.py`/`test_system_store_repository.py` tests still pass.
- `SystemStore` had no guarded way to (a) update an existing document on an encrypted
  structure (only `insert_one()` existed) or (b) delete from one at all. Added
  `stamp_expiry()` (can only ever `$set` the `expires_at` field — cannot smuggle a
  plaintext payload past `EncryptionGuard` because it never touches `_envelope` or any
  other field), `replace_one()` (guarded by the same `EncryptionGuard.check_document`
  as `insert_one`), and `delete_many()` (no guard needed — deleting cannot write a
  plaintext payload the way inserting/replacing could).
- No concrete `EnvelopeEncryptor` implementation existed anywhere (the module's own
  docstring deferred real KMS-backed encryption to "Phase 9"). Rather than declare
  `reasoning_checkpoints`/`reasoning_checkpoint_writes` `encrypted: true` without any
  way to actually encrypt them, built `AesGcmEnvelopeEncryptor` — real AES-256-GCM,
  unique nonce per message, authenticated — as an explicitly-interim implementation
  (same spirit as `LoggingAuditSink`/`AllowlistRedactor` from Phase 5), pending a real
  KMS-backed one.

**Deliberately not built (scope boundaries, not gaps):**
- No business reasoning graph (Order Discovery's, the Analyzer's) — later phases.
- No real `AgentAiPort`/`KnowledgePort` binding behind the receipt state machine's
  `external_ref` resolution — a future phase's node does the actual AI Gateway/sync
  call; this package only keeps that call idempotent across resumes.
- `ai_interceptions` (checked by the abandonment precondition checker) has no real
  writer yet — correctly finds nothing today, will correctly start blocking once a
  future AI Gateway interception phase wires real records into it.

**Gate receipts.**
- `poetry run mypy src` / `.venv/Scripts/python.exe -m mypy src`: 44 errors in 14
  files — unchanged baseline (now checked across 410 source files, up from 388).
- `ruff format --check` / `ruff check` on every new/changed file: clean.
- `pytest tests/reasoning/ -q` (real Mongo + real Temporal, via a throwaway
  `python:3.13-slim` + `uv` container attached to the compose network, matching Slice
  3R's established pattern): all 22 tests across the 12 specified files pass —
  4 architecture tests, 8 mechanism tests including the two genuinely real-Temporal
  crash-recovery/idempotency tests.
- `pytest tests/platform/test_index_drift.py tests/platform/test_system_store_bootstrap.py
  tests/platform/test_system_store_repository.py`: all 18 pass unchanged — the TTL/
  guarded-write extensions to `system_store` didn't regress Slice 3R's invariants.
- `python scripts/dev/precommit_guard.py`: one initial false-positive flag (a
  docstring's prose mentioning "NotImplementedError" while describing the *base
  class's* inherited behavior, not a stub in this code) — reworded rather than
  overridden; clean on re-run.
- `python scripts/dev/run_changed_gate.py`: all gates pass except the same
  pre-existing `backend-mypy` baseline noted above.
- `pytest -q` full host suite (excluding `tests/platform`/`tests/configuration`/
  `tests/reasoning`/`test_order_agent_rest.py`, with placeholder `NVIDIA_API_KEY`/
  `GOOGLE_API_KEY` values unblocking the fixture): 1449 passed, 3 skipped, zero
  failures.
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.

**New verified fact:** `uv add`/`uv lock` in this repo create (and populate via
`poetry install --sync`, which auto-detects an in-project `.venv`) a real, working
`backend/.venv` — shorter-pathed and simpler to reference than the prior session's
Poetry cache-directory venv, and fully equivalent (`ruff check` produced byte-identical
output against both for the same diff). Used as the primary host venv for this slice.

## Phase 6 / Wave B3 — Temporal return orchestration (this slice)

**Scope.** User explicitly chose "build the full stage/handler config engine" over a
narrower completion-bar-only option. Discovery (an Explore agent plus direct reading of
`operations/orchestrator.py` (701 lines) and `workflows/return_workflow.py` (412 lines)
in full) surfaced a materially different picture than the plan text assumes, which
shaped what "config-driven" honestly means here:

- `ReturnWorkflow` (the Temporal workflow) is a clean, generic, business-logic-free
  stage-sequence tracker. `ReturnOrchestrator` (an external polling coordinator, not the
  Temporal workflow itself) is what runs the actual per-stage business logic
  (`AIGatewayService`, `ReturnSupportService`, `SQLBusinessStateRepository`,
  `FeedbackLearningService`) and drives `ReturnWorkflow` forward via
  `handle.execute_update(ReturnWorkflow.complete_stage, ...)`.
- **None of the six Phase-5 agents are invoked anywhere in `orchestrator.py`'s real
  business logic.** The closest candidate for a clean AGENT substitution
  (`ReturnWorkflowAgent.assess()`, used at the internal-support work-item branch) needs
  per-line-item detail — `shippedQuantity`, `attachmentIds` — that `ReturnSessionView`
  doesn't carry (only flat `itemReferences: list[str]`). Forcing that mapping would be an
  independent, unreviewed business-logic redesign, not "make sequencing config-driven" —
  so `orchestrator.py`'s stage business logic was deliberately left unchanged.
- **Two entirely separate configuration systems coexist** and are not bridged:
  `ReturnPlatformConfiguration` (`configuration/return_configuration.py`, loaded from one
  file, `config/returns/production.yaml` — what `orchestrator.py`/`AgentRegistry.build()`
  actually consume today) vs. `RuntimeSnapshot`/`RuntimeConfigurationView`
  (`configuration/domain/release_model.py` + the manifest/release system —
  where `config/workflows/return_session.yaml` lives). `AgentExecutionContext.configuration`
  is typed `RuntimeConfigurationView`, but agent *construction*
  (`OrderDiscoveryAgent(configuration: ReturnPlatformConfiguration)`) takes the other one
  — this duality predates this slice and was not resolved here; bridging the two is a
  separate, larger migration.
- Two stage-name lists already existed in config and neither matched the 8-value
  `WorkflowStage` enum `ReturnWorkflow`/`ReturnOrchestrator` actually use:
  `config/returns/production.yaml`'s `workflow.stages` (15 values, exactly
  `ProductionReturnStage`'s order — a different, signal-driven orchestration track via
  `ProductionReturnWorkflow`/`ProductionWorkflowCoordinator`) and
  `config/workflows/return_session.yaml`'s stale 7-value list (`ORDER_SELECTION`,
  `FULL_ORDER_SYNC`, `LINE_CONFIRMATION`, `ORDER_ANALYSIS`, ... — read by no code
  anywhere, confirmed via grep).

**What was built.**
- `configuration/domain/workflow.py` — `WorkflowStageHandlerType` (AGENT/ACTIVITY),
  `WorkflowStageHandler` (`type`, optional `agent`), `WorkflowStageEntry` (`stage`,
  optional `handler`); `WorkflowDefinition.stages` widened from `list[str]` to
  `list[str | WorkflowStageEntry]` with new `stage_ids()`/`handler_for(stage_id)`
  helpers. **Fixed a real, live gap found in passing:**
  `validator.py::_validate_workflows` (§3.6) already checked
  `stage.handler.type == "AGENT"` against a raw dict — but `WorkflowDefinition.stages:
  List[str]` could never actually hold a dict, so `compatibility.py`'s
  `WorkflowDefinition(stages=stages_raw, ...)` would have raised an unhandled
  `pydantic.ValidationError` (not the clean `ConfigurationValidationError` AGENT modules
  get) the moment any WORKFLOW module's YAML used a structured handler — that validator
  code was unreachable dead code. Fixed by widening the schema, wrapping
  `WorkflowDefinition(...)` construction in the same try/except AGENT gets, and updating
  `_validate_workflows` to check the now-real `WorkflowStageEntry`/`WorkflowStageHandler`
  types instead of a dict shape that could never exist.
- `workflows/return_workflow.py` — `_STAGE_SEQUENCE`/`_NEXT_STAGE` (hardcoded module
  constants) replaced with `DEFAULT_STAGE_SEQUENCE` (same 8 values, now just a default)
  and a new `ReturnWorkflowInput.stage_sequence` field. `ReturnWorkflowExecutionState`
  carries `stage_sequence`; `start_return_workflow_execution` validates it (non-empty,
  ≤32 stages, no duplicates, all real `WorkflowStage` values) and sets
  `current_stage = stage_sequence[0]` (previously hardcoded to `INTAKE`);
  `advance_return_workflow` computes `next_stage` from `dict(pairwise(state.stage_sequence))`
  and treats `stage_sequence[-1]` as the terminal/COMPLETED marker, both now genuinely
  configurable instead of fixed. `ReturnWorkflow.run()`'s persistence-mismatch check
  compares against `self._state.current_stage` instead of a hardcoded `WorkflowStage.INTAKE`.
  Removed two leftover `DEBUG` log lines found adjacent to the code being edited.
- `operations/orchestrator.py` — new optional `workflow_definition: WorkflowDefinition
  | None` constructor parameter; `_stage_sequence_from_definition()` resolves a pinned
  `WorkflowDefinition`'s `stage_ids()` into `WorkflowStage` order. Defaults to
  `DEFAULT_STAGE_SEQUENCE` when not supplied (today's exact prior behavior, zero
  behavior change for existing callers) — `orchestrator.py` doesn't yet hold a
  `RuntimeConfigurationView` reference, so this is an honest opt-in, not a fabricated
  bridge between the two configuration systems.
- `config/workflows/return_session.yaml` — rewritten to the real 8-value `WorkflowStage`
  sequence, each stage declared `handler: {type: ACTIVITY}` (honestly matching that
  `orchestrator.py`'s business logic isn't agent-routed). Full reconciliation mapping
  and the "why not AGENT yet" reasoning is documented in the file's header comment.
- `config/workflows/example_agent_dispatch.yaml` (new, registered in `manifest.yaml`) +
  `tests/configuration/test_workflow_agent_handler_resolution.py` — a real, isolated,
  honestly-scoped fixture proving the AGENT-handler mechanism end-to-end: builds the
  actual `RuntimeSnapshot` via `LegacyCompatibilityAdapter` against the real
  `backend/config` manifest, resolves a `WorkflowStageHandler(type=AGENT,
  agent="order_discovery")`, then resolves and executes the real `OrderDiscoveryAgent`
  through `AgentRegistry.resolve()` with a real `AgentExecutionContext` (real
  `SystemClock`, `InMemoryCapabilityRegistry`, a minimal real in-test `AuditSink`/
  `Redactor`). Satisfies the plan's literal Wave B completion condition ("one configured
  workflow can resolve an agent implementation through the canonical registry and
  execute it under a pinned configuration release") without overstating that the live
  return-session workflow already dispatches through it.
- `tests/api/test_no_generic_advance_endpoint.py` (new `tests/api/` directory) —
  enumerates every real router under `return_platform.api` (via `pkgutil`) and asserts
  no route path contains "advance", except the one confirmed, explicitly-excluded
  exception (`dependency_simulator.py`'s `/operations/{operation_id}/advance`, a
  synthetic dev/test harness unrelated to real `ReturnSession`s). A second test guards
  the exclusion itself from going stale.
- `tests/test_return_workflow.py` — updated the one test that enumerates
  `ReturnWorkflowExecutionState`'s fields exhaustively (now includes `stage_sequence`);
  added three new tests: a differently-configured (shorter) stage sequence produces
  genuinely different real behavior (fewer required stages, earlier `COMPLETED`), and
  two rejection tests (empty sequence, duplicate stage in sequence).

**Deliberately not done this slice (documented scope boundaries, not gaps):**
- `orchestrator.py`'s per-stage business logic (AI evaluation, support ticket creation,
  bay assignment, feedback recording) was not rewritten to route through
  `AgentRegistry` — no clean, honest 1:1 request-shape mapping exists today for 6 of the
  7 real stages (see discovery notes above). Making *sequencing* config-driven and
  proving the *mechanism* for AGENT dispatch real were treated as this slice's true
  scope; deeper business-logic-to-agent mapping is a separate, larger call.
- The `ReturnPlatformConfiguration` ↔ `RuntimeSnapshot`/`RuntimeConfigurationView`
  bridge was not built — `orchestrator.py` still constructs its agents/business services
  from the legacy single-file config. `_stage_sequence_from_definition()` exists and is
  tested in isolation, ready for whoever builds that bridge.
- `ProductionReturnWorkflow`/`ProductionWorkflowCoordinator` (the separate 15-stage,
  signal-driven track) was not touched — it already used `AgentRegistry.build().<attr>`
  from Phase 5, and its stage list is a distinct lifecycle model with no natural mapping
  to `WorkflowStage`.

**Gate receipts.**
- `.venv/Scripts/python.exe -m ruff format --check` / `ruff check` on every new/changed
  file: clean. Two pre-existing-dirty files touched (`compatibility.py`, `validator.py`)
  confirmed via before/after diff to carry the exact same 22 pre-existing lint errors and
  format debt both before and after this slice's edits — zero new debt introduced; the
  lines this slice actually added/edited are format- and lint-clean.
- `.venv/Scripts/python.exe -m mypy` on all 5 changed source files: `Success: no issues
  found`.
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.
- `pytest tests/test_return_workflow.py tests/test_return_session_persistence.py
  tests/configuration/ tests/api/ -q`: all pass, including the 6 new tests (3 in
  `test_return_workflow.py`, 2 in `tests/api/`, 1 in `tests/configuration/`).
- `pytest -q --ignore=tests/test_order_agent_rest.py` (full host suite, no API-key
  placeholders): 1561 passed, 3 skipped, 67 errors — all 67 are the same pre-existing
  `NVIDIA_API_KEY not set` fixture gap noted in the Phase 5A ledger entry, not
  regressions (confirmed: this slice touches none of `platform/system_store`,
  `platform/reasoning`, or anything `tests/platform`/`tests/reasoning` import — `git
  diff --stat` for this slice lists only `configuration/`, `workflows/return_workflow.py`,
  `operations/orchestrator.py`, and test/config files under those same trees).

## Phase 8 / Wave C1 — Canonical read-only source connector framework (this slice)

**Scope.** User explicitly chose "attempt the full consolidation now" over a
narrower "harden what exists" option, after discovery found the ask was bigger than
the plan text implies: a mature canonical connector layer already existed
(`dynamic_knowledge/connectors/{mongodb,sqlserver}.py`) with most of what the plan
asks to "create" already built, but **five separate read/write implementations**
existed against the same two physical systems, **four parallel source-configuration
schemas**, and **zero real-Docker-based tests** anywhere for any connector (existing
tests used hand-written fakes). A Plan-agent design pass then found the real picture
was bigger still: `targeted_read()` had **zero implementations anywhere** (not just
duplicated), `data_platform/graph/sync_service.py` runs its own separate, deliberately
interim `ActiveSchema` bridge (`interim_active_schema.py`, explicitly scoped to a
later "Step 12" cutover — not touched here), and v2's real query shapes (OR-of-
conditions, regex, cross-collection resolution) don't fit the AND-only
`LogicalTargetedReadPlan` model at all.

**What was built.**
- **New neutral package `source_connectors/`** (`contracts.py`, `protocols.py`,
  `identifiers.py`, `path_resolution.py`, `compilation.py`, `mongodb.py`,
  `sqlserver.py`) — dynamic_knowledge-independent (only depends on
  `dynamic_knowledge.schema.ActiveSchema` as a shared *type*, not its graph/sync
  machinery), so `data_platform`/`data_console`/`v2` no longer need to reach into
  `dynamic_knowledge` internals just to read Mongo/SQL Server.
- Split `dynamic_knowledge/on_demand_sync/contracts.py` (previously a mixed-concern
  file): source-*read* contracts (`SourceCursor`, `CursorComparison`,
  `RawSourceDocument`, `RawSourcePage`, `SourceConnectorCapabilities`,
  `LogicalAnchorCondition`, `LogicalTargetedReadPlan`) moved to
  `source_connectors.contracts`, re-exported unchanged from the old location so no
  existing importer needed to change. Graph-*mutation* contracts
  (`DynamicSourceRecord`, `GraphNodeMutation`, etc.) stayed put. New types:
  `DatasetRef`, `BoundedSamplePolicy`. `SourceConnectorCapabilities` gained
  `supports_point_lookup`/`supports_bounded_sample` (additive, defaulted).
- `SourceScanConnector`/`SourceScanRegistry` (from `sync/coordinator.py`) and
  `TargetedSourceConnector`/`ConnectorRegistry` (from `on_demand_sync/coordinator.py`)
  — previously defined inline — moved to `source_connectors.protocols`, imported
  back where used. New additive `PointLookupConnector` protocol.
- `source_connectors/compilation.py` — moved (byte-identical) from
  `dynamic_knowledge/on_demand_sync/source_compilers.py`, which had **zero
  production consumers** (confirmed via grep) and exactly one test importer
  (updated in place, no shim needed). `MongoDBSourceScanConnector.targeted_read()`
  is the **first real caller** of `compile_source_read`'s MongoDB branch anywhere —
  closing the "zero implementations" gap, reusing the existing AND-only condition
  compiler rather than re-deriving filter translation.
- `dynamic_knowledge/connectors/{mongodb,sqlserver}.py` — kept as thin re-export
  shims (real implementations moved to `source_connectors`); zero of their ~10
  existing internal importers needed to change.
- New shared primitives on `source_connectors.mongodb`/`.sqlserver`:
  `fetch_one`/`find_many`/`sample_documents` (Mongo), `fetch_row`/`sample_rows`
  (SQL Server) — bounded point-lookup, filtered-multi-read, and offset-paginated
  sample reads, all server-side bounded.
- `path_resolution.py` — extracted the ~90%-duplicate `_resolve_physical_field`/
  `_resolve_physical_column` logic from both connectors into one shared
  `resolve_physical_path()` (parameterized by error type + wording, preserving each
  connector's exact existing error messages/types).
- `identifiers.py` — extracted the SQL-Server-only `_SAFE_IDENTIFIER` validator;
  `data_console/api/browser.py`'s previously-separate copy of the same pattern now
  shares it (byte-identical regex, confirmed via test).
- **Redirected 3 of the 5 read implementations onto the canonical connectors:**
  - `data_console/api/browser.py` — `_get_sql_records`/`_get_mongo_records`/
    `_get_sql_record`/`_get_mongo_record` now call `sample_rows`/`sample_documents`/
    `fetch_row`/`fetch_one` instead of raw `pymssql`/`pymongo`. Fixed a live
    cross-module private-function import (`api/data_source_config_v2.py` was
    importing `_get_mongo_records`/`_get_sql_records` directly) by making both
    functions public. Found and fixed a real bug while touching this file: removing
    the unused-looking `_identifier`/`pymssql`/`asyncio`/`copy` imports during the
    redirect initially dropped `import copy`, which `_redact_document` actually
    needs (`copy.deepcopy`) — no test caught it (zero prior test coverage for this
    file); caught by mypy, fixed.
  - `data_platform/sources/mongodb/customer.py` — `CustomerMongoSourceAdapter`'s
    one `find_one` call redirected onto `fetch_one` (with `max_time_ms`/`comment`
    passed through exactly). No live caller exists for this adapter today; user
    chose to do this redirect anyway for consistency. All existing hardening
    (bounded timeout, depth/node freeze budget, BSON-hash evidence, error taxonomy)
    left untouched around the one redirected line.
  - `v2/runtime_adapters.py` — `MongoOrderSourceGateway` (**highest-risk change**:
    live order-sync business logic). Its real query shapes (OR-of-conditions,
    regex fallback, cross-collection tracking-number resolution) do not fit
    `LogicalTargetedReadPlan`'s AND-only model — extending that model into a
    proper condition-tree would be a separate, much larger redesign, so instead
    added a new, honest, generic `find_many(database, *, collection_name, filter,
    limit)` primitive: v2 keeps its own business query-shape knowledge (correctly,
    per the plan's own "business identity synthesis... does not belong in the
    generic connector"), but no longer holds a raw `pymongo` dependency. All 4
    raw-driver call sites (`_sales_ids`, `resolve()`'s TRACKING_NUMBER/
    INVOICE_NUMBER branches, `fetch()`) redirected; `fetch()`'s previously-fully-
    unbounded query given an explicit `10_000`-document safety ceiling (a real,
    minor, newly-added bound, not a preserved behavior).
- **Not redirected (documented scope boundary):** the write-only seeding adapter
  (`data_platform/operational_generation/adapters/source_mongodb.py`) — a synthetic
  test-data-generation path, not a production read, correctly out of scope for a
  "read-only source connector" phase.
- **Real Docker-based tests added** (previously zero existed for any connector):
  `tests/source_connectors/test_mongodb_connector_docker.py` (6 tests: scan,
  targeted_read, fetch_one, find_many, sample_documents, a real ObjectId-cursor
  round-trip), `tests/source_connectors/test_sqlserver_connector_docker.py` (3
  tests: scan, sample_rows, fetch_row — run directly on host, SQL Server is a
  standalone instance reachable via the compose port mapping), and
  `tests/v2/test_mongo_order_source_gateway_docker.py` (8 tests covering `fetch()`
  and all 5 `AnchorType` resolution paths plus account-scope filtering) — the
  **first ever test coverage of any kind** for `MongoOrderSourceGateway`, given
  its zero prior coverage was the biggest risk in this slice. All 17 Docker tests
  pass. Existing fake-based unit tests (`tests/dynamic_knowledge/test_{mongodb,
  sqlserver}_connector.py`) kept unchanged, still passing, as fast edge-case
  coverage alongside the new real-infra proof. Added `PLATFORM_TEST_SQLSERVER_HOST`
  to `tests/conftest.py`'s `test_settings()` fixture (previously hardcoded
  `"localhost"`), matching the existing `PLATFORM_TEST_MONGO_HOST` pattern.

**Deliberately not done this slice (documented scope boundaries, not gaps):**
- Config-schema reconciliation across the 4 parallel surfaces
  (`configuration/domain/sources.py`'s `SourceConfigNode`/`SourcesConfig` — confirmed
  dead code, zero runtime consumer; `dynamic_knowledge.schema.SourceAssetDefinition`
  — live, stays canonical; `data_platform.schema_registry.SchemaRegistry` — live,
  scoped to write-policy/generation governance, deliberately not merged into
  `ActiveSchema`; `data_platform.mapping`'s `source_assets` catalog — intentionally
  non-executable per its own YAML comment) was not attempted. No PostgreSQL source
  connector was added — zero real implementation or consumer exists anywhere
  (`ConnectorType.POSTGRESQL` is an unused enum value; the running Postgres
  container is Temporal's own storage, not a business source).
- `data_platform/graph/sync_service.py`'s separate `interim_active_schema.py`
  bridge was left exactly as-is — its own docstring already documents it as a
  deliberate, temporary bridge pending a later, separately-scoped cutover.
- `LogicalTargetedReadPlan`'s AND-only, single-entity condition model was not
  redesigned into a proper AND/OR condition tree — v2's real query shapes need
  that redesign to be served by the "canonical" plan model honestly, but doing so
  would mean touching the Neo4j/SQL/Mongo branches of `compile_source_read`, the
  planner, and the extraction pipeline for a capability only one caller needs
  today; flagged for whoever next needs OR-shaped targeted reads.

**Gate receipts.**
- `.venv/Scripts/python.exe -m ruff check`/`ruff format --check` on every
  new/changed file: clean (2 pre-existing-dirty files carrying forward unchanged
  debt from the files they were moved from, confirmed via before/after diff).
- `.venv/Scripts/python.exe -m mypy src`: 44 errors in 14 files — unchanged
  baseline (confirmed via git-stash before/after comparison; the two connector
  files' 3 pre-existing errors and `sync_service.py`'s 4 pre-existing errors moved/
  stayed byte-identical, zero new errors anywhere touched).
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.
- `pytest tests/ -q --ignore=tests/test_order_agent_rest.py --ignore=tests/platform
  --ignore=tests/reasoning --ignore=tests/configuration` (host, real API-key
  placeholders, excluding the 2 new Mongo-only Docker test files which cannot reach
  `mongodb:27017` from host): 1457 passed, 3 skipped, zero failures.
- Real-infra verification via a throwaway `python:3.13-slim`+`uv` container
  attached to `return-multi-agent-platform_platform` (Mongo replica set
  unreachable from host by design; SQL Server verified directly on host instead,
  since it's a standalone instance reachable via the compose port mapping, not a
  replica set): all 14 new Mongo Docker tests + all 3 new SQL Server Docker tests
  pass. Also ran the full suite inside the container (1630 passed; the only
  failures were pre-existing tests that depend on `repo_root/scripts/` files this
  container's `backend/`-only copy doesn't include — an artifact of the container
  setup, not a regression).
- `tests/dynamic_knowledge/` + `tests/data_platform/`: 307 passed, 2 skipped,
  confirming the mechanical extraction (Slice 0) and the customer-adapter/
  compilation-move redirects (Slices 2–3) are zero-behavior-change where intended.

## Next READY slice

Per the FINAL_FAST_EXECUTION_PLAN, Wave C1's stated goal ("do this before Analyzer
work so source access is not implemented twice") is now met for the real production
read paths. Still open, not part of this slice: the flagged Neo4j volume dedup task,
the pre-existing `openapi-drift`/`associate_flow.py` formatting conditions, the
missing `NVIDIA_API_KEY`/`GOOGLE_API_KEY` values in `.env`, a real KMS-backed
`EnvelopeEncryptor` (Phase 9), the `ReturnPlatformConfiguration` ↔ `RuntimeSnapshot`
configuration-system bridge, mapping `orchestrator.py`'s real per-stage business
logic onto agents where a clean request-shape mapping can be honestly designed, the
4-way source-config schema reconciliation, and the `LogicalTargetedReadPlan`
AND/OR condition-tree redesign v2's full query shape would need to be served by the
canonical targeted-read model rather than the new `find_many` escape hatch.
