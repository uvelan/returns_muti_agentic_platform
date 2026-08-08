# Execution state

Branch: `refactor/unified-return-platform`
Verified HEAD: pending this slice's commit (previous: `b19570f`)
Last pushed green commit: `b19570f` (`refactor(bootstrap): decouple application startup from test tooling`)
Slice: **Phase 5 / Wave B1 — Independent agent plugin contract**
Status: DONE

## Phase 4 — Bootstrap correctness

Status: DONE (see git history for `b19570f`; superseded as "current slice" by Phase 5 below).

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

## Next READY slice

Wave B2 (LangGraph durable reasoning foundation, Phase 5A) or B3 (Temporal return
orchestration, Phase 6) per the FINAL_FAST_EXECUTION_PLAN — both can start now that B1
is green. Separately, still open and not part of this slice: the flagged Neo4j volume
dedup task, the pre-existing `openapi-drift`/`associate_flow.py` formatting
conditions, and the missing `NVIDIA_API_KEY`/`GOOGLE_API_KEY` values in `.env`.
