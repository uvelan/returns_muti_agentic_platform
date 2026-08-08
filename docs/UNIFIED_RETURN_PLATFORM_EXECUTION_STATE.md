# Execution state

Branch: `refactor/unified-return-platform`
Verified HEAD: `a37976286266f64f2eed3ddc1fac0918fcb33e3b` (a379762)
Last pushed green commit: `a379762` (`feat(platform): add configuration-driven system store bootstrap`)
Slice: **3R — SystemStore correctness hardening**
Status: DONE

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

## Next READY slice

Phase 4 — Bootstrap correctness (SQL migrations 003/004, remove `seed-runner` dependency,
uv/poetry dependency unification). Slice 3R is DONE; no P0 SystemStore invariant remains
open.
