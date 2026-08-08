# Execution state

Branch: `refactor/unified-return-platform`
Verified HEAD: pending this slice's commit (previous: `65106ce`, then `fbfcf05` security fix)
Last pushed green commit: `fbfcf05` (`security(tests): remove hardcoded live NVIDIA/Google API keys from test fixture`)
Slice: **Phase 4 — Bootstrap correctness**
Status: DONE

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

## Next READY slice

Wave B (agents/orchestration) per the FINAL_FAST_EXECUTION_PLAN, once the flagged Neo4j volume
issue is separately resolved. Phase 4 is DONE; no open blocker in code introduced by this slice.
