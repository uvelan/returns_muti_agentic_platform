# Execution state

Branch: `refactor/unified-return-platform`
Last pushed green commit: `19c356b` (H1 - mypy 20 -> 8)
Slice: **Waves F and G are complete. Wave H1 is done** (`a84718f`, unpushed).
Status: The cutover is finished and the static gate is clean for the first time in this
programme's history. What remains is H2 (behavioural campaign) and H3 (final acceptance).

**H1 closed the static gate.** mypy: **0 errors across 470 source files**, down from 42/14
at Wave D, 20 at the start of H1. Not one error was silenced -- see the H1 section below.

**Wave F, end to end:** contract 223 -> 107 paths; router mounts 42 -> 22; backend source
files 496 -> 470; frontend 229 -> 48 files and 76+4 routes -> 4. Every test-count drop is a
deleted subject, not lost coverage.

**Open, and worth someone's attention:**
`test_return_workflow_concurrency.py::test_a_second_completion_sees_the_first_ones_state`
failed **2 of 11** full-suite runs while passing consistently in isolation, and has not
been caught under capture, so there is no diagnosis -- only two eliminated hypotheses
(activity timeout: 10s budget vs a 0.2s stub; mutex hole: the `while` re-check is sound,
`_transition_in_progress` is set synchronously, `finally` always clears). It predates
Wave F. It is unreproduced, not fixed, and the distinction matters.

Suite: **2065 passed, 7 skipped** via `bash backend/scripts/dev/run_real_infra_suite.sh` —
which, until this session, had never actually run: it failed on MSYS path mangling and then
on a missing `.env` mount. Both fixed; see the live-walkthrough section below. Every earlier
"suite green" line in this ledger was measured some other way.
Frontend: eslint clean, `tsc -b` clean, 65 unit tests, 6 Playwright e2e.
Backend static: ruff check + format clean; **mypy clean**; `import return_platform.main` ok.
Contract: **107 paths**; drift PASS in verify mode at an unchanged hash.

"Green" now means the **full** gate. `scripts/linux/03_run_backend_quality.sh` runs
`ruff check .` and `ruff format --check .` from `backend/`, and both pass across all 739
files as of `870e066`. Every prior entry in this ledger reported green on the *changed-files*
gate (`scripts/dev/run_changed_gate.py`), which is a narrower claim; read older entries with
that in mind. Wave G/H's "full static integrity" (Phase 29) is now a much smaller job than
this ledger previously implied — what is left there is mypy's 42, not ruff's 246.

## Reset tooling, and a committable reference dataset

Status: DONE (unpushed). The reset pieces existed; the sequence did not, and two
steps in it did not exist at all.

**Nothing loaded the new data, and nothing built the graph.**
`07_seed_and_validate_data.sh` seeds the old synthetic manifest. The scripts that
produced the working copilot -- wipe, load 100 real orders, derive products and
customer parties from them, then run `GraphSyncService` -- were written in a
scratchpad and never committed. No script in the repository called
`GraphSyncService` at all, so a fresh environment left Neo4j empty and the
copilot searched a graph with no nodes: a truthful "no orders found" that reads
as a broken agent. Now `backend/scripts/load_reference_dataset.py`,
`backend/scripts/build_knowledge_graph.py`, and `scripts/linux/reset_all.sh`
chaining the six steps in the one order that works.

**A third gap the sequence exposed:** step two's `docker compose down --volumes`
takes the Vault volume with it and nothing puts it back, so every later step
fails on `Required Vault secret is unavailable` -- pointing at secrets
management rather than at the reset that removed them. `reset_all.sh` seeds
Vault between infrastructure and load.

**And a fourth, found by running the loader from `backend/`:**
`PLATFORM_VAULT_TOKEN_FILE` in `.env` is a *relative* path, so Vault resolution
silently depends on the working directory. Both scripts now anchor themselves to
the repository root rather than making that the caller's problem.

### De-identifying the dataset: the proof found four leaks the denylist missed

The extract is production data -- customer and staff names, street addresses,
phone numbers, and further down cheque account numbers, driver licence numbers,
card-holder addresses and payment tokens. It is committed **scrubbed**, with the
document structure preserved exactly, because the active schema reads through it
by physical path and a flatter approximation extracts nothing.

`deidentify_reference_dataset.py` matches keys by pattern, which over four
hundred field names is a losing game on its own. What makes it trustworthy is
the proof that follows: every value the source held under a sensitive key is
searched for in the output, and a survivor fails the run. It caught, in order:

- `custPONumber` and `jobName` holding the customer's **site address** -- free
  text fields that no name-shaped pattern covers.
- `pickedEmpId` holding `"AUSTIN WILSON"`, and `relManualHold` audit strings with
  an employee's name embedded mid-string. Employees are people too; a scrub
  aimed only at customers leaves half the record.
- `drvLicNum` and `tagInfo` each holding a **phone number** -- and `drvLicNum`
  was missed by a pattern that already contained `licen`.

The last of those is why contact details are now matched by *shape* as well as
by key, across every value the key list decided to keep. Chasing key names one
at a time was the wrong shape of fix.

Two rounds of the proof's own false positives were worth fixing rather than
suppressing: a value that also appears under a non-sensitive key (a product
description reachable from a name-shaped field) proves nothing by being present,
and a value drawn from a six-item vocabulary can land on itself, which means the
scrubber ran rather than skipped. Output is byte-reproducible -- the first
version used Python's per-process-salted `hash()`, which would have made every
regeneration an enormous meaningless diff.

`backend/tests/test_reference_dataset.py` (7 tests) holds the committed file to
both properties it exists for: no real contact details, and all three
collections still join.

**Verification.** Load and graph build run clean end to end: 100 orders, 482
products, 97 customer parties, 572 lines; `COMPLETED`, 1756 nodes, 2641
relationships, 8 constraints. Graph queried directly afterwards: PLACED_ORDER
100, HAS_ORDER_LINE 572, REFERENCES_PRODUCT 507, HAS_CONTACT 298, HAS_ACCOUNT 97.
Full canonical suite **2065 passed, 7 skipped**; ruff and mypy clean on all three
new scripts.

**The concurrency flake has a second face.** This run,
`test_return_workflow_concurrency.py::test_concurrent_stage_completions_are_serialised`
failed under full-suite load and then passed 3/3 in isolation -- the same
signature as `test_a_second_completion_sees_the_first_ones_state`, in the same
file, still undiagnosed. Nothing in this slice touches the return workflow. It
remains unreproduced and unfixed, and it is now known to affect more than one
test in that file.

## Live copilot walkthrough — three defects the tests could not have found

Status: DONE (unpushed). Driven end to end in a browser against the reloaded 100-order
dataset, with the MANUAL provider holding each reasoning request so a human authored the
`AgentAction` a model would have produced. Every defect below was found by *running* the
thing; each had passing tests either side of it.

**1. Neo4j temporal values never survived the read boundary.**
`Neo4jKnowledgeGateway.execute` returned `dict(record)` straight from the driver, so a
property typed `DATETIME` arrived as `neo4j.time.DateTime`, not `datetime`. Three separate
consumers treat a result row as plain JSON — `sha256_digest` canonicalizes it for the
evidence checksum, the reasoning context serializes it into the prompt, the API response
encodes it — and all three raise `TypeError: Object of type DateTime is not JSON
serializable`. An `ORDER_SEARCH` on `sales_order` selects `order_date`, so this was most
searches, and it failed the whole turn rather than degrading.

Fixed at the adapter, in `_json_safe`, not in the canonicalizer. `sha256_digest` is a
generic function with no business knowing what a graph driver is; teaching it about
`neo4j.time` would also have left the same types loose in the prompt and the HTTP body.
Driver types belong to the adapter and stop at its edge. Temporal and `Duration` values
become `iso_format()`, `Point` keeps its SRID rather than degrading to a bare array, bytes
become hex.

*Not fixed, and deliberately:* `graph_repository.py` reads configuration nodes via
`result.data()` and `generation_writer.py`/`neo4j_writer.py` via `dict(record)`. Those are
lifecycle and count reads, not user-data property reads, and none is on this path — but
they have the same exposure if a temporal property is ever selected through them.

**2. The Order Agent route was the only route without the `{data, meta}` envelope.**
It returned a bare `AgentTurnResult`; `frontend/src/api/client.ts` rejects any
non-enveloped body outright. So a turn that reasoned correctly all the way to an
evidence-cited answer — 200, complete, four resolved citations — surfaced in the UI as
"the server returned an invalid API response envelope." The failure read as the agent and
was the response shape. `orderAgent.ts` already did `response.data`: the client was written
to the convention, and the route had simply never adopted it. Now
`APIResponse[AgentTurnResult]`; `tests/test_order_agent_rest.py` reads `["data"]`; all four
OpenAPI snapshots re-exported (the exporter writes **one** path per invocation, which is
why running it once leaves the other three drifted) and the drift check is back to PASS.

**3. The identified order was derived per-turn, so a follow-up question erased it.**
`candidateRows(turn)` read only the latest turn's evidence. Asking "and has it shipped"
runs a `GRAPH_QUERY` whose evidence carries `rows` and no `candidates`, so the context pane
reset to "the agent has not matched an order yet" and the progress rail walked *backwards*
off **Orders identified** — the associate had lost nothing, only asked a question. The
identified candidate set belongs to the conversation, not to one turn. `turnCandidates` now
returns `null` for "this turn did not search" as distinct from `[]` for "this turn searched
and found nothing", and the page keeps the last real answer. Both halves are tested: the
follow-up keeps the match, an empty later search clears it.

**On the walkthrough itself.** The MANUAL provider resolves to
`DurableInterceptionProvider` in any process holding a SystemStore, so held requests land
in `platform_ai_interceptions` (sealed) and **not** in `.manual_llm/` — worth knowing
before hunting for files that will never appear. `/api/ai/interceptions` lists them,
`/{id}/request` unseals one, `/{id}/answer` takes `{"responseText"}` only; `answeredBy`
comes from the capability check, and sending it is a 422. Two turns ran green: a partial
name plus a partial product description resolved to MELGON HEATING & COOLING and order
CW273354 (IND DRFT MTR ASSY, qty 1), then a follow-up returned its status and shipping
method. `HallucinationGuard` verified every citation, including numeric path segments as
decimal strings (`["rows","0","order_date"]`).

Frontend: eslint clean, `tsc --noEmit` clean, **93 tests passing** across 14 files.
Contract drift: PASS. Backend `ruff`/`mypy` clean on the changed files.

### And four more, from running the test suite against the rewritten schema

The schema rewrite that mapped the entities onto the real salesInv documents left the
search strategy pointing at fields of the *previous*, synthetic schema. Nothing crashed:
`SchemaQueryGuard` rejects an unknown field, `order_search` logs
`order_search_plan_rejected` at DEBUG and moves to the next plan. So whole search
strategies were silently absent, and the only symptom was a search that found less than it
should have.

- **`_DATE_FIELD_ID` was `delivered_at`, which does not exist.** The real documents carry
  order, commit, invoice and shipped dates; there is no delivery timestamp. Every
  date-bearing search — the associate's "it was around the 14th", which the design lists as
  a primary discovery signal — was dropped. Now `order_date`: the only date populated on
  every order and the one an associate is most likely to recall. **This is a judgement
  call**: if a genuine delivery timestamp is ever sourced, that constant is where it goes.
- **The misspelling fallback selected `customer_key`**, a surrogate of the old schema. The
  probe plan was rejected outright, so fuzzy customer matching — explicitly asked for —
  never ran at all. Now `account_id`, `customer_id`, `customer_name`; `candidate_key`
  already identifies a customer row by `customer_id`.
- **`order_date` and `ordered_quantity` had no `searchable_by`.** In the rewrite
  `searchable: false` was set to mean "not free-text", but the guard reads it as "may not
  appear in any filter". Both are signals progressive search is built around.
- **Three of the nine strong anchors were inert**, including the combination anchor
  `probable_order_by_customer_and_status`, which the rewrite dropped entirely. That anchor
  *is* the "strong combination of fields" escalation: two weak signals together
  (customer + status) authorise a targeted on-demand sync when neither alone would. The
  other two, `exact_customer_in_account` and `exact_order_in_account`, existed but named an
  `account_id` that was not flagged `on_demand_sync_anchor` — declared, and dead.

The last of those had no detector, so `test_no_strong_anchor_is_inert` now asserts the
invariant across the whole schema rather than anchor by anchor: a declared anchor whose
fields do not permit on-demand sync is a config defect that loads cleanly and shows up only
as a sync that never happens.

`tests/dynamic_knowledge/test_search_strategy.py`: **22 passed** (was 20, with 5 failing).
`test_order_line_now_has_a_strong_anchor` was rewritten to the line's real composite key
(branch + order + line number) rather than the removed synthetic `order_line_key`.

### The misspelling fallback ran, and still found nothing

With the probe plan repaired, a live turn for "melgan heatng" reached the fuzzy matcher and
returned **zero** candidates. `MELGON HEATING & COOLING` scores 0.667 on a whole-string
`difflib` ratio, under the 0.72 threshold — while the next best customer in the same 100-row
probe scores 0.370. The name matched; the untyped tail diluted it. Trade names carry
suffixes an associate never types ("& COOLING", "INC", "SERVICES"), which is precisely the
population this fallback exists to serve, so a whole-string ratio was the wrong measure.

`_similarity` now also slides a window of the query's length across the candidate and keeps
the best (0.786 here), leaving the threshold alone — the threshold is what keeps 0.370 out,
and lowering it instead would have admitted noise. Windows only, never the reverse: scoring
a short query against every substring of a long name would match "smith" into "smithson
plumbing" and a dozen more, so the comparison stays anchored to what the associate typed.

Verified live: the same message now returns MELGON HEATING & COOLING (OHVAL) *and* KOSEL AC
& HEATING INC (DALLAS), both tagged `customer_name_fuzzy` — two genuinely similar names, so
the agent hedged the spelling and asked which of the two by name, exactly as the prompt
requires of a fuzzy match. The existing rejection tests still pass unchanged.

### The canonical real-infra runner had never actually run

`backend/scripts/dev/run_real_infra_suite.sh` failed at two layers on Windows, and both were
invisible until someone ran it:

- Under Git Bash, MSYS rewrites any argument that looks like an absolute POSIX path, so
  `/opt/venv/bin/python` reached Docker as `C:/Program Files/Git/opt/venv/bin/python` and the
  exec failed with a "no such file or directory" that reads like a broken image rather than a
  mangled argument. `MSYS_NO_PATHCONV=1` on the exec; no effect on Linux or macOS.
- Past that, `tests/conftest.py::pytest_configure` hard-fails without the repository `.env`,
  and the `diagnostics` service mounted only `./backend`. The suite aborted with an
  INTERNALERROR before collecting a single test. The service now also mounts `./.env`
  read-only, which does not reintroduce the host-port problem its `environment:` block
  solves: `load_dotenv(override=False)` leaves already-exported variables alone.

Running it then produced six failures, all in the seed generator, and four more real
defects behind them.

### The seed generator, once the suite could see it

**The guard could not read a nested record.** `validate_proposal` iterated
`record.items()` and compared top-level keys against a map of dotted registry names, so
once records were properly nested every field of every record was reported as an unknown
field and `WritePlanner.build_plan` refused the whole proposal — the production write path,
not just a test. `_declared_paths` now addresses a record by the paths the registry names,
stopping descent at a declared field so `customer.address` stays one leaf, and all three
checks (unknown field, required field, natural key) share that one view. Flat records still
validate identically; the guard simply no longer has an opinion about document shape.

**The synthetic-identity policy had quietly stopped applying.** The dispatch tested
`field.name == "email"` / `"phoneNumber"` / `"customerName"` — field names of the earlier
flat schema. The one phone field is now
`salesHdr.salesHdrData.shipping.shipTo.address.shipToPhone`, so nothing matched and it fell
through to the generic fallback, writing the literal string `"Deterministic <path>
<seed>-<index>"` where a phone number belongs. Dispatch is on `field.generator` now: that
is the declared intent, and it does not move when a path does.

**Two joins that could never form.** The CDM mints its customer id inside `party[]`, and
the resolver is fed only from *string* field values — so it was never published, salesInv
minted its own, and the documented bridge
(`party[].custAccts[].additionalCustomerInfo[].customerId` → `custId`) joined nothing.
Separately, `_sales_lines` took `masterProductId` from `master_product_reference`
(`fopt.mstrProdId`, a composite of id and warehouse) while `line_references_product` matches
it against `product.product_id`, whose physical path is `_id`. Every CustomerParty and every
Product would have been an orphan, from seed data that reads as entirely plausible. This is
the same class as the `_nest_dotted_keys` defect and the mismatched relationship match
fields before it: the seed looked right and joined to nothing.

**Nineteen generators the registry declared were not supported by the AI Studio.**
`_value` raises on an unimplemented name, so those assets could not be generated through
that path at all. All nineteen are implemented against the Ferguson vocabularies the seed
generator already uses (`_LITERAL_GENERATORS`), including `customer_addresses` and
`cdm_parties` with the nesting their joins depend on. `SUPPORTED_GENERATORS` is synced in
both directions, since the invariant the test asserts is exact agreement — three names the
studio still claimed and nothing declares are drift too.

Tests were corrected where they encoded the old flat shape, not where they were right:
`record_paths.at` / `leaf_paths` read a document by registry path, and the relationship test
now asserts the joins the active schema actually declares rather than the ones the flat seed
happened to satisfy.

**Full canonical suite: 2059 passed, 7 skipped, 0 failed** (4m11s), via
`bash backend/scripts/dev/run_real_infra_suite.sh` — the first time that script has run to
completion. `ruff check` and `ruff format --check` clean across `data_platform`; mypy clean
on every changed module.

## Phase 7 / Wave C2, Commit 1 — Foundations

Status: DONE (see git history for `8d39923`; superseded as "current slice" by Commit 2 below).

## Phase 8 / Wave C1 — Canonical read-only source connector framework

Status: DONE (see git history for `7fd10ad`; superseded as "current slice" by Phase 7 below).

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

- ~~`openapi-drift` fails~~ **RESOLVED** (see the regeneration section above): the committed `openapi/return-platform.openapi.json` snapshot
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

## Phase 7 / Wave C2 — Order Discovery LangGraph decomposition + Temporal host (this slice, Commit 1: Foundations)

**Scope.** User explicitly chose "build full on-demand sync wiring in this pass"
(over deferring it) and "build a real Temporal workflow host" (over keeping the
existing synchronous route) for the wider Wave C2 slice. A Plan-agent design pass
(mirroring Phase 8's discovery-then-plan approach, given this is the largest single
phase attempted this session) found two genuine, previously-undiscovered correctness
bugs that had to close before any on-demand-sync production wiring could be trusted —
closing them, plus building the remaining foundation pieces the LangGraph/Temporal
work (Commits 2–3) will build on, is this commit's scope.

**Two real bugs found and fixed (would have silently broken correctness):**
- `MongoGraphStateProvider.active_generation()`'s legacy fallback derived
  `f"legacy-{schema.configuration_checksum[:20]}"`, while
  `data_platform/graph/sync_service.py` marks its Neo4j `GraphGeneration` node under
  the fixed literal `"legacy-live"` — these never matched, so any on-demand-sync write
  against a not-yet-generation-managed schema would fence-reject forever. Fixed by
  introducing one shared constant, `LEGACY_GENERATION_ID` (`dynamic_knowledge/graph/
  generation.py`), and pointing both sides at it (`mongo_store.py`'s fallback;
  `sync_service.py`'s own literal already matched, now via the shared name). Proven
  against real Mongo + real Neo4j in `tests/dynamic_knowledge/
  test_mongo_graph_state_provider.py` and, end-to-end, in this commit's own new
  `test_on_demand_sync_production_wiring.py` (see below).
- `source_connectors.compilation.compile_source_read`'s SQL Server branch emitted
  `:name`-style (colon) bind parameters, but `pymssql` only accepts `%(name)s`
  pyformat style — every targeted SQL Server read would have raised a driver-level
  syntax/parameter error at the first real call. Fixed by translating at the SQL
  Server connector boundary (a `_COLON_PARAMETER` regex substitution in
  `source_connectors/sqlserver.py`, not by changing `compile_source_read`'s shared
  output shape, which Mongo's branch also depends on). Also implemented
  `SqlServerSourceScanConnector.targeted_read()` itself, which had no implementation
  at all before this commit (`CAPABILITIES.supports_point_lookup` was already `True`
  with nothing behind it). Proven against real SQL Server:
  `test_targeted_read_returns_only_the_matching_row`.

**New platform primitives built for Commits 2–3 to consume:**
- `AgentPolicy` (`dynamic_knowledge/schema.py`) gained `max_clarifications` (default
  3), `max_replans` (default 2), `max_targeted_syncs_per_turn` (default 3) — bounds
  the LangGraph decomposition's new CLARIFY/REPLAN/on-demand-sync loop turns will
  need. `ActionType` (`order_agent/contracts.py`) gained `CLARIFY`/`REPLAN` members
  with matching `validate_action_payload` requirements. `active-schema.example.yaml`/
  `active-schema.return-order.yaml` updated with the three new fields;
  `configuration_checksum` recomputed for both and verified to load cleanly through
  `load_active_schema()`. Dead `order_agent/prompt_policy.py` (zero importers,
  confirmed via grep) deleted.
- `platform/reasoning/evidence_store.py` (new) — `QueryEvidenceStore`, the "evidence
  by reference" half of "no raw source records in checkpoint state": full
  `QueryEvidence` (including its raw `result`) is written once, encrypted, keyed by
  `query_execution_id`; a LangGraph checkpoint will hold only that id. New
  `order_discovery_query_evidence` SystemStore structure (`config/platform/
  system_store.yaml`, `encrypted: true`, TTL index). `CheckpointRetentionPolicy.
  mark_terminal` (`retention.py`) extended to stamp this structure's `expires_at`
  alongside checkpoints/writes/receipts, in the same transaction, keyed by `run_id`.
- `platform/reasoning/run_lifecycle.py` (new) — `ReasoningRunLifecycle`, the
  previously-missing write path for `reasoning_runs` (`start_run` — idempotent
  insert, raises `RunBoundToDifferentThread` on a genuine conflict;
  `transition_non_terminal` — raises `ValueError` directing terminal transitions to
  the existing `CheckpointRetentionPolicy.mark_terminal` instead).
- **On-demand-sync production adapters** (previously `OnDemandSyncCoordinator` was
  constructed nowhere in `src`, only in a test against local fakes):
  `MongoOnDemandSyncStore` (`integration/mongo_store.py`, reserve/complete keyed by
  `request_digest`, mirrors `MongoAtomicConversationStore`'s optimistic-insert
  shape); `OnDemandConnectorRegistry`/`OnDemandNeo4jGraphWriter` (new
  `integration/on_demand_sync_adapters.py` — the registry dispatches by
  `ConnectorType` to dedicated long-lived Mongo/SqlServer `targeted_read`
  connectors; the writer wraps `Neo4jDynamicGraphWriter` + `Neo4jGenerationWriter.
  get_status()`, looking the current fencing token/status up fresh on every write
  since an on-demand caller doesn't carry one through its own state machine the way
  a full-sync run does). `GenericGraphProjector` already satisfied
  `DynamicGraphProjector`'s protocol exactly — no adapter needed. `runtime_factory.
  build_dynamic_order_agent_runtime()` now constructs the full stack and passes it
  as `on_demand_sync=` (was hardcoded `None`); gained a required `source_mongo`
  parameter, threaded from `main.py`'s existing `resources.source_mongo` (added to
  the dynamic-agent dependency-availability check alongside `mongodb`/`neo4j`).
- **Generation-handle design decision (confirmed, not built):** kept the existing
  simple `active_generation()` string-returning mechanism rather than building the
  formal, currently-unused `GenerationReadLease`/`GenerationWriteReservation` lease
  system already declared in `graph/generation.py` — confirmed nothing in the repo
  drains those leases, and `lifecycle/orchestrator.py`'s own docstring says real
  lease-draining "must be added before this runs against live traffic," so building
  an unused formal system now would be speculative.

**Real-infra test added:** `tests/dynamic_knowledge/
test_on_demand_sync_production_wiring.py` (4 tests, real Mongo + real Neo4j) proves
the full production stack end-to-end — a real document read via
`MongoDBSourceScanConnector.targeted_read()`, projected via `GenericGraphProjector`,
written via `OnDemandNeo4jGraphWriter` and verified present in Neo4j — and that the
write path fails closed (`NoGenerationMarker`) when no marker exists, and fails
closed when the target `graph_generation_id` doesn't byte-for-byte match the marker
it was created under (the exact failure mode the `LEGACY_GENERATION_ID` fix
addresses). Deliberately does not touch the real shared `"legacy-live"` Neo4j marker
(uses fresh per-test UUID-suffixed stand-in ids instead), since that literal is
shared, live state other suites/dev workflows depend on. Deliberately bypasses the
shared `test_settings` fixture (constructs its own Mongo DSN / Neo4j URI from the
same underlying env vars) because that fixture also requires `NVIDIA_API_KEY`/
`GOOGLE_API_KEY` for AI-gateway fields this test never exercises — those keys are
unavailable in this environment (see below), and this test has no need of them.
Stable across 3 consecutive runs.

**Known pre-existing blocker (not introduced by this slice, already flagged in
Phase 8's ledger entry above, re-confirmed here):** `NVIDIA_API_KEY`/`GOOGLE_API_KEY`
were rotated out of `.env` by the separate `fbfcf05` security commit and never
replaced with real values. Every test that uses `tests/conftest.py`'s shared
`test_settings` fixture — not just this slice's new tests — now errors at fixture
setup on this machine (confirmed: 46 errors across `tests/dynamic_knowledge`,
`tests/reasoning`, `tests/source_connectors`, `tests/v2`, all identical
`RuntimeError: Required test environment variable is not set: NVIDIA_API_KEY`, zero
relation to any code this slice touched — 268 tests in the same run passed cleanly).
Not fixable here; the user needs to add rotated key values. This will block Commit
3's Temporal-workflow-host tests if they end up needing a live model gateway call
end-to-end, unless resolved first.

**Deliberately not done in this commit (scope boundary, not a gap — Commits 2–3):**
- No LangGraph node decomposition of `DynamicOrderAgentCoordinator` yet (Task #78).
- No Temporal workflow host for Order Discovery conversations yet (Task #79).
- No `api/order_agent.py` route change, no `GENERATION_CHANGED` signal handling —
  both depend on the Temporal host existing first.

**Gate receipts.**
- `ruff format --check` / `ruff check` on every new/changed file (backend + this
  commit's new test file): clean.
- `mypy src`: 46 errors in 15 files, i.e. +2 errors / +1 file versus the 44/14
  baseline at HEAD (`7fd10ad`) — confirmed via a real git-stash before/after
  comparison, not estimated. Both new errors are the identical, pre-existing
  `GraphDriver`/`GenerationDriver` vs. `AsyncDriver` structural-typing false
  positive already present for `sync_service.py`'s equivalent
  `Neo4jDynamicGraphWriter(driver, ...)` construction (a real neo4j-driver
  Protocol-matching limitation, not a real type error) — same class of finding,
  not a new category. `mypy` on the new test file separately shows the same 2 (test
  files are outside the `mypy src` baseline scope).
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.
- `pytest tests/dynamic_knowledge/test_on_demand_sync_production_wiring.py -v` (real
  Mongo + real Neo4j, via the `c2-test-runner` throwaway container attached to
  `return-multi-agent-platform_platform`, `PLATFORM_TEST_MONGO_HOST=mongodb` +
  `PLATFORM_TEST_NEO4J_HOST=neo4j`): 4 passed, stable across 3 consecutive runs.
- `pytest tests/dynamic_knowledge tests/reasoning tests/source_connectors tests/v2 -q`
  (same container): 268 passed, 46 errors — all 46 the pre-existing `NVIDIA_API_KEY`
  fixture gap above, zero relation to this commit's diff.

## Phase 7 / Wave C2, Commit 2 — LangGraph node decomposition (this slice)

**Scope.** Decomposed `DynamicOrderAgentCoordinator.process_turn()`'s ~710-line
imperative for-loop into a compiled `langgraph.graph.StateGraph`, per a Plan-agent
design pass (mirroring Phase 8/Commit 1's discovery-then-plan approach given this was
the largest single phase attempted this session). The design surfaced one significant,
previously-unbuilt gap — nothing in `src` wired a real `SystemStore`/`EnvelopeEncryptor`
into app startup, only test fixtures constructed them — user explicitly chose to build
this as step 0 rather than stub it or defer it, so the whole slice is verifiable
end-to-end against real Mongo.

**Step 0 — real SystemStore bootstrap at app startup (previously missing entirely).**
New `platform/system_store/manifest_loader.py` loads `config/platform/system_store.yaml`
directly into bootstrap-ready `StructureDefinition`s via a **local** pydantic payload
model (`_SystemStoreConfigPayload`/`_SystemStoreStructurePayload`) rather than importing
`configuration.domain.system_store.SystemStoreConfig` — `platform/*` must never import a
domain module (design doc §13.1, rule R2a, enforced by
`tests/platform/test_layering.py::test_platform_imports_no_domain_module`); this was
caught and fixed after an initial version imported the domain type directly, which is
exactly why that architecture test exists. Deliberately bypasses the full configuration
release/manifest pipeline (DRAFT/VALIDATED/APPROVED/ACTIVE approval semantics) — the
system-store manifest declares which Mongo collections/indexes exist, not a versioned
business-schema release. `main.py` gained `_bootstrap_reasoning_system_store()`, called
from the existing `dynamic_agent_enabled` block before `build_dynamic_order_agent_runtime`,
constructing a real `SystemStoreBootstrapper` (winner/waiter/takeover, matching Slice
3R.8's established pattern exactly) and a real `AesGcmEnvelopeEncryptor`. New
`Settings.reasoning_encryption_key`/`reasoning_encryption_key_secret_reference` (base64,
validated to decode to exactly 32 bytes; production mode requires the Vault reference and
rejects the dev-default value, matching `validation_fingerprint_key`'s established
pattern) and `Settings.system_store_manifest_path`.

**The LangGraph decomposition itself.**
- `dynamic_knowledge/order_agent/errors.py` (new) — `OrderAgentFailure` moved out of
  `coordinator.py` into its own module so `graph_nodes.py` and `coordinator.py` can both
  import it without a circular dependency; re-exported from `coordinator.py` unchanged
  for every existing importer (`api/order_agent.py`, `runtime_factory.py`).
- `dynamic_knowledge/order_agent/state.py` — new `OrderAgentGraphState` TypedDict (every
  field a bounded/model-generated value or a reference/id/counter, never raw `QueryEvidence`)
  and the literal `ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST` frozenset `CheckpointRedactor`
  enforces against every checkpoint write. A dedicated pure test
  (`test_order_agent_graph_state.py`) asserts the allowlist and the TypedDict's own keys
  never drift apart, and that `CheckpointRedactor` genuinely rejects an extra key.
- `dynamic_knowledge/order_agent/graph_nodes.py` (new, ~950 lines) — 10 node functions
  (`decide`, `validate_action`, `out_of_scope`, `get_schema`, `graph_query`,
  `order_search`, `request_on_demand_sync`, `clarify`, `replan`, `respond`) plus routing
  functions, each a 1:1 port of the equivalent original `process_turn()` branch. Static
  dependencies (guards, gateways, the compiler, the evidence store) close over via a
  `GraphDependencies` dataclass built once at graph-compile time; per-invocation data that
  must never be checkpointed (`GuardContext`, carrying principal/tenant/role information)
  flows through LangGraph's `Runtime.context` (`TurnRuntimeContext`), never through state.
  Evidence-by-reference: every node that produces `QueryEvidence` calls
  `EvidenceStore.put(run_id=..., evidence=...)` and stores only `query_execution_id` in
  `evidence_refs`; `decide`/`respond`/`clarify` rehydrate full evidence via `get_many()`
  immediately before calling the model gateway or `HallucinationGuard`. New budget gates
  (`max_clarifications`, `max_replans`, `max_targeted_syncs_per_turn` — declared in
  Commit 1, unconsumed until now) each raise a new, additive `OrderAgentFailure` code
  (`ORDER_AGENT_CLARIFICATION_BUDGET_EXCEEDED`/`ORDER_AGENT_REPLAN_BUDGET_EXCEEDED`/
  `ORDER_AGENT_SYNC_BUDGET_EXCEEDED`) on top of every existing code/message/retryable
  flag reproduced verbatim. `CLARIFY` is a same-turn terminal action (not a LangGraph
  `interrupt()`) — the associate's follow-up answer becomes a brand-new turn/thread,
  keeping `thread_ids.py`'s "one thread per turn" invariant intact and needing no
  Temporal wiring; flagged as a small, swappable seam (a `ClarificationStrategy`-shaped
  extension point) for Commit 3 to later upgrade to a real `interrupt()`/`Command(resume=...)`
  pattern without a graph topology change. `REPLAN` resets `evidence_refs`/
  `order_search_cache` but preserves every turn-wide budget counter (a replan cannot be
  used to bypass ceilings). `CandidateSet` (built in an earlier phase, previously zero
  production consumers) is now wired for real: `order_search` builds one per fresh search
  with a 30-minute TTL, embedded in `order_search_cache["candidateSet"]` (no new store —
  ids/checksums/timestamps only, comparable sensitivity to what `orderSearchCache` already
  persisted unencrypted); a new `AgentAction.selected_candidate_id` field +
  `LogicalQueryPlan.candidate_set_id`-triggered validator rule let `graph_query` resolve
  and ground a later "the second one"-style reference against it via
  `CandidateSet.validate_selection()`, routed through the same correction protocol as any
  other guard rejection.
- **Two real bugs found and fixed while wiring `CandidateSet`, not part of the original
  ask:** `search_strategy.rank_search_results`'s dedup-key fallback was `str(id(row))` —
  Python object identity, never stable across process restarts or even two calls in the
  same process for equivalent data, so it could never serve as a `CandidateSet` member a
  later turn's `validate_selection()` could match against. Fixed with a new shared
  `candidate_key(row)` helper (sales_order_number/customer_id/sku, falling back to a
  deterministic `sha256_digest(row)`), used identically by `rank_search_results` and the
  fuzzy-customer-fallback path (previously built candidates with no key at all). Each
  candidate's output dict gained a `candidate_id` field so callers never need to
  re-derive the key.
- `dynamic_knowledge/order_agent/graph.py` (new) — `build_order_agent_graph(deps,
  checkpointer=...)`, wiring/topology only; every node's behavior lives in
  `graph_nodes.py`. `out_of_scope` is reached directly from routing, never through
  `respond`'s guards — preserving a real dead-code finding from the original
  coordinator.py (an `OUT_OF_SCOPE` branch inside the RESPOND handler that was provably
  unreachable, since `OUT_OF_SCOPE` was always intercepted earlier in the loop).
- `dynamic_knowledge/order_agent/coordinator.py` (rewritten, same public class name and
  `process_turn(request, guard_context) -> AgentTurnResult` signature — a true drop-in
  replacement, zero changes needed in `api/order_agent.py`) — now builds the compiled
  graph once at construction (real `SystemStoreCheckpointSaver`), and `process_turn`
  owns exactly what the graph must not: conversation load/commit (unchanged), a real
  `ReasoningRunLifecycle.start_run()` per turn (`thread_id = run_id =
  ReasoningThreadIdFactory.order_discovery_thread_id(conversation_id, client_turn_id,
  attempt=1)`), invoking the graph with `recursion_limit=256` (LangGraph's default of 25
  is comfortably exceeded by policy's own allowed ceiling on `max_reasoning_steps`, up to
  32, given each policy-enforced loop turn spans several LangGraph super-steps), and
  `CheckpointRetentionPolicy.mark_terminal(..., COMPLETED)` on success /
  `..., FAILED)` on any exception (re-raised unchanged after stamping) — the only place
  run-lifecycle bookkeeping happens, keeping the graph itself free of it.
- `dynamic_knowledge/integration/runtime_factory.py` — `build_dynamic_order_agent_runtime`
  now constructs `QueryEvidenceStore(system_store, reasoning_encryptor)` and threads
  `system_store`/`envelope_encryptor`/`mongo_client` into the rewritten coordinator.

**Test suite rewrite.** The old fake-based `tests/dynamic_knowledge/test_order_agent.py`
could no longer construct a coordinator with pure fakes (real `SystemStore`/checkpointer/
run-lifecycle are now load-bearing constructor dependencies) — deleted, superseded by:
- `test_order_agent_graph.py` (new, 8 tests, fake gateways + LangGraph's `InMemorySaver`,
  no real infra needed) — ports all 3 of the old file's scenarios byte-for-byte
  (model-failure-has-no-fallback, query-then-respond, miscased-capability-corrected) onto
  the compiled graph directly, plus 5 new scenarios covering behavior that didn't exist
  before this commit: `OUT_OF_SCOPE` fails closed before any capability check, `CLARIFY`
  ends the turn with `requested_input` set, `REPLAN` resets evidence and continues
  reasoning, `max_reasoning_steps` budget enforcement, and `ORDER_SEARCH` completing
  cleanly to an empty-result cache (the shared `active_schema` fixture only defines
  `entity_a`/`entity_b`, not the real-world entities `search_strategy.py` hardcodes, so
  every progressive plan is guard-rejected — a genuine, pre-existing test-fixture
  limitation, not something this commit could fix without touching the shared fixture;
  `test_search_strategy.py`'s own tests already cover real scoring/matching).
- `test_order_agent_coordinator_real_infra.py` (new, real Mongo via the `c2-test-runner`
  throwaway container) — proves the coordinator *wrapper* (not just the graph) works
  end-to-end: bootstraps a uniquely-suffixed copy of the real system-store manifest,
  builds a real `SystemStore`/`AesGcmEnvelopeEncryptor`/real `MongoAtomicConversationStore`/
  `MongoGraphStateProvider` (fake model/knowledge gateways — no live AI/Neo4j needed to
  prove this specific wiring), runs one full turn, and asserts a real `reasoning_runs`
  document exists with `lifecycle_state=COMPLETED` and `expires_at` set, and a real
  encrypted `reasoning_checkpoints` document exists for the thread. Stable across 3
  consecutive runs.
- `test_reasoning_system_store_bootstrap.py` (new, step-0 verification) — the real
  manifest loads and includes the reasoning structures with `encrypted: true` where
  declared; the dev-default reasoning key decodes to exactly 32 bytes and round-trips
  through `AesGcmEnvelopeEncryptor`; a uniquely-suffixed copy of the real manifest
  bootstraps against real Mongo, a second bootstrap reuses structures and creates
  nothing, and a real encrypted document written through the bootstrapped `SystemStore`
  round-trips through the real encryptor.

**Self-inflicted process note (corrected before commit):** a `ruff format .`/`ruff check .`
invocation was mistakenly run repo-wide instead of scoped to this slice's files,
reformatting ~90 unrelated files (pre-existing formatting debt untouched by this
session) and introducing one worse-than-before cosmetic change. Caught immediately via
`git status`; every unintended file was restored with `git checkout --` before staging,
confirmed by diffing the resulting file list against the intended Commit 2 change set
and re-running the full gate on exactly that list.

**Deliberately not done this commit (scope boundary, not a gap — Commit 3):**
- No Temporal workflow host — `coordinator.process_turn` is still invoked synchronously
  from `api/order_agent.py`, unchanged.
- No `GENERATION_CHANGED` signal handling, no `interrupt()`-based CLARIFY resume across
  HTTP requests — both require the workflow host Commit 3 builds.
- `ReasoningObservability` (structured logging/metrics around graph execution) was not
  wired into the coordinator — a separable concern layerable later without changing the
  core checkpoint/evidence/lifecycle mechanics built here.

**Gate receipts.**
- `ruff format --check` / `ruff check` on the exact 16-file Commit 2 change set: clean.
- `mypy src`: 47 errors in 15 files — **unchanged** from the Commit 1 baseline (confirmed
  identical count both before and after this commit's full diff, including after the
  `ruff format .` incident was corrected). All new occurrences are the same two
  already-accepted false-positive classes documented in Commit 1 (`GraphDriver`/
  `GenerationDriver` vs. `AsyncDriver` structural-typing; frozen `StructureDefinition` vs.
  `_StructureLike` Protocol's implicit mutable-attribute variance) — zero new categories.
- `python -m compileall -q src` / `python -c "import return_platform.main"`: clean.
- `pytest tests/ -q` (real Mongo + real Neo4j, via the `c2-test-runner` throwaway
  container attached to `return-multi-agent-platform_platform`, excluding
  `test_order_agent_rest.py` and three container-artifact-only files that depend on
  `repo_root/scripts/` this `backend/`-only container copy doesn't include —
  `test_ai_model_probe_evaluator.py`, `tests/gate_tools/`, `test_runtime_env_key_sync.py`,
  all confirmed pre-existing per Phase 8's own ledger entry): **1566 passed, 2 skipped, 0
  failed**, 99 errors — every one of the 99 confirmed to be the same pre-existing
  `NVIDIA_API_KEY`/`GOOGLE_API_KEY` fixture gap flagged in Phase 8's ledger entry (spot-
  checked several directly), zero relation to this commit's diff.
- `test_order_agent_graph.py` (8/8), `test_order_agent_coordinator_real_infra.py` (1/1,
  stable across 3 runs), `test_reasoning_system_store_bootstrap.py` (3/3, stable across 3
  runs), `test_order_agent_graph_state.py` (3/3): all pass in isolation and as part of
  the full suite.

## Phase 7 / Wave C2, Commit 3 — Temporal workflow host for Order Discovery (this slice)

**Scope.** Per the user's explicit "proceed" (endorsing a faster-path plan proposed in
response to "best way to finish faster without dropping quality"): defer the
`interrupt()`-based CLARIFY resume upgrade (CLARIFY stays a same-turn terminal action,
unchanged from Commit 2) and mirror `ReturnWorkflow`'s established Temporal shape as
closely as the two workflows' different natures allow, rather than re-deriving Temporal
idioms from scratch. Moves turn processing for Order Discovery conversations out of
synchronous in-process coordinator calls into a durable Temporal workflow — one workflow
execution per conversation, one Activity per turn.

**New Temporal host.**
- `workflows/order_discovery_workflow.py` (new) — `OrderDiscoveryWorkflow`
  (`@workflow.defn(name="return-platform-order-discovery-v1")`): `run()` records
  `conversation_id`/`agent_id` then waits forever (`wait_condition(lambda: False)`) — a
  conversation has no defined end the way a Return's stage sequence reaches COMPLETED, so
  unlike `ReturnWorkflow` this workflow never reaches a terminal state (a new, accepted
  standing cost: every conversation accumulates a permanently-running workflow execution;
  continue-as-new/retention-sweep flagged as future follow-up, not built here).
  `execution_state` query for observability. `generation_changed` signal records
  `_last_known_graph_generation_id` only — deliberately an "observable no-op" that does
  not gate or short-circuit `submit_turn`, because Neo4j's own generation fencing and
  `HallucinationGuard`'s generation check already make a mid-turn generation change safe
  without any workflow-level intervention (same "real mechanism, no real caller yet"
  precedent as CLARIFY/REPLAN in Commit 2). `submit_turn` update calls the
  `run_order_discovery_turn` Activity (10-minute timeout, `maximum_attempts=1` — a failed
  turn is a structured result, not something Temporal should blindly retry).
- **Real concurrency bug found and fixed while building the `submit_turn` mutex, not part
  of the original ask.** The obvious translation of `ReturnWorkflow.complete_stage`'s
  guard —
  `await workflow.wait_condition(lambda: not self._turn_in_progress); self._turn_in_progress = True` —
  is unsafe. Temporal's `wait_condition` (`_workflow_instance.py::workflow_wait_condition`)
  never checks its condition synchronously; it always registers `(fn, future)` and
  suspends, and `_run_once` later evaluates *all* pending conditions in one batch pass
  before any of the newly-released continuations get to run. Two concurrent `submit_turn`
  calls admitted into the same workflow task can both observe `not self._turn_in_progress`
  as true in the same batch (neither has mutated it yet), both get released together, and
  both proceed to flip the flag and call `execute_activity` — the "mutex" does not
  serialize them. Caught concretely by a real-Temporal test
  (`test_submit_turn_mutex_serializes_concurrent_submissions`): two `asyncio.gather`'d
  `execute_update` calls produced `max_concurrent_calls == 2` against the stub activity.
  Fixed by re-validating the guard in a loop after every wait instead of trusting a single
  `wait_condition` call:
  ```python
  while self._conversation_id is None or self._turn_in_progress:
      await workflow.wait_condition(
          lambda: self._conversation_id is not None and not self._turn_in_progress
      )
  self._turn_in_progress = True
  ```
  Stable across 5 consecutive runs after the fix. **`ReturnWorkflow.complete_stage` uses
  the identical unsafe pattern and was not touched in this commit** (out of scope — it's
  already-shipped code from Phase 6) — flagged as a separate follow-up task
  (`task_92c35ace`) rather than bundled in here, since fixing it requires its own
  real-Temporal regression test and commit.
- `workflows/order_discovery_activities.py` (new) — `OrderDiscoveryActivities(coordinator,
  schema)`. `run_order_discovery_turn` looks up the agent's policy directly off the held
  `schema` (no coordinator call needed for an unknown `agent_id`), reconstructs
  `GuardContext`/`PrincipalContext` from the minimal identity fields that travel across
  the Temporal boundary (`principal_id`/`tenant_id`/`roles`/`branch_ids` — never the full
  `ActiveSchema`/`GuardContext`, mirroring the old `runtime_factory.py` `guard_context_factory`
  closure, now removed from there since the FastAPI process no longer needs it), and
  converts a known `OrderAgentFailure` into a structured `OrderDiscoveryTurnOutcome.error`
  return value rather than raising across the Activity boundary — only genuinely
  unexpected exceptions become real Activity failures.
- Data-converter strategy: deliberately did **not** configure
  `temporalio.contrib.pydantic.pydantic_data_converter` on the shared Temporal `Client`
  (also used by `ReturnWorkflow`/`ProductionReturnWorkflow`) — `AgentTurnResult` (a
  pydantic model) crosses the Temporal boundary as an opaque `model_dump_json()` string
  inside a plain dataclass field, decoded via `AgentTurnResult.model_validate_json(...)`
  at the FastAPI route. **Confirmed real, minor wire-fidelity note**: the default JSON
  converter has no representation for `frozenset`, so `SubmitOrderDiscoveryTurnCommand.roles`/
  `.branch_ids` (`frozenset[str]`) come back as plain `list[str]` after a real
  encode/decode round trip — harmless here because the only consumer,
  `PrincipalContext` (a pydantic model), coerces the list back to `frozenset[str]` on
  construction, but a strict dataclass `==` after decode would incorrectly fail; the round-trip test
  (`test_temporal_default_converter_round_trips_workflow_contracts`) asserts on
  `frozenset(decoded.roles) == command.roles` instead of dataclass equality, documenting
  why.
- `workflows/order_discovery_worker.py` + `scripts/run_order_discovery_worker.py` (new) —
  `ORDER_DISCOVERY_TASK_QUEUE = "return-platform-order-discovery-v1"`,
  `create_order_discovery_worker`. The worker script mirrors
  `run_return_workflow_worker.py`'s shape exactly: both Mongo clients, Neo4j driver,
  connectivity checks, `bootstrap_reasoning_system_store()` (see below),
  `build_dynamic_order_agent_runtime(...)`, `load_active_schema(...)`, heartbeat loop,
  cleanup in `finally`.
- `dynamic_knowledge/integration/reasoning_bootstrap.py` (new) — `bootstrap_reasoning_system_store()`
  extracted verbatim out of `main.py`'s former private `_bootstrap_reasoning_system_store`,
  now shared by both the FastAPI process and the new worker script.

**Architectural simplification discovered mid-implementation.** Since turn processing now
runs entirely inside the worker process's Activity, the FastAPI process no longer needs a
`DynamicOrderAgentCoordinator`, `SystemStore` bootstrap, or Mongo/Neo4j dependency checks
for the order-agent feature at all — only a Temporal `Client` + the task queue name. This
cascaded through three files: `main.py`'s `dynamic_agent` startup block shrank to
constructing a `DynamicOrderAgentRuntime(temporal_client, task_queue)` and no longer calls
`bootstrap_reasoning_system_store`/`build_dynamic_order_agent_runtime` at all;
`dynamic_knowledge/api/order_agent.py` was rewritten around
`client.start_workflow(...)` + `except WorkflowAlreadyStartedError: get_workflow_handle(...)`
+ `execute_update(OrderDiscoveryWorkflow.submit_turn, command)` (the exact pattern
`operations/orchestrator.py` already uses for `ReturnWorkflow`); `runtime_factory.py`'s
`build_dynamic_order_agent_runtime` now returns the plain `DynamicOrderAgentCoordinator`
instead of wrapping it in a `DynamicOrderAgentRuntime` (that type now lives in
`api/order_agent.py` with a different, Temporal-facing meaning), and its
`guard_context_factory` closure was deleted (reconstruction now happens once, in the
Activity).

**Other wiring.**
- `coordinator.py::process_turn` gained an optional `workflow_id: str | None = None` kwarg,
  passed to `ReasoningRunLifecycle.start_run(...)`, so `platform/reasoning/abandonment.py`'s
  "active Temporal workflow" precondition (previously always a no-op for order-discovery
  runs, since `workflow_id` was always `None`) becomes meaningful once a real workflow
  exists. Direct/test callers with no Temporal workflow of their own leave it unset.
- `Settings.order_discovery_workflow_task_queue` (new field, same
  not-programmatically-linked-to-the-worker's-hardcoded-default convention
  `return_workflow_task_queue` already uses — deliberately mirrored rather than
  "fixed", for consistency with established style) + a new `order-discovery-worker`
  service block in `compose.yaml` (mirrors `return-workflow-worker`'s dependencies:
  `runtime-configuration-init`, `mongodb-rs-init`, `temporal`, `neo4j`).

**Deliberately not done this commit (documented scope boundary, per user's "proceed").**
- No `interrupt()`-based CLARIFY resume across HTTP requests — CLARIFY is still a
  same-turn terminal action, exactly as Commit 2 left it.
- No full LLM-driven end-to-end test (Workflow → Activity → real `DynamicOrderAgentCoordinator`
  → real LangGraph → real Mongo commit). The graph's entry node (`decide`) always calls
  the model gateway — there is no LLM-free fast path even for exact-identifier queries —
  and the pre-existing missing `NVIDIA_API_KEY`/`GOOGLE_API_KEY` values (already blocking
  `test_order_agent_rest.py` and ~99 other tests, flagged at Phase 8's and Commit 2's
  checkpoints) make this infeasible in this environment. Not worked around with a mock
  model gateway, since that would not be a real-infra proof. The coordinator's own real
  wiring (real Mongo, real checkpoint/run-lifecycle persistence) is already proved by
  Commit 2's `test_order_agent_coordinator_real_infra.py`; what Commit 3 adds on top
  (the Temporal mutex, signal, and crash-survival mechanics) is proved directly against
  real Temporal instead, without needing the LLM call.
- No continue-as-new or retention sweep for the never-terminating workflow — flagged as a
  known, accepted standing cost, not a gap to close later in this phase.

**Test suite (all against real, running infra — no fakes/mocks of Temporal itself).**
- `test_order_discovery_workflow.py` (new, 6 tests) — data-converter round-trip
  (documents the frozenset→list wire behavior above), sandbox-preparability
  (`SandboxedWorkflowRunner().prepare_workflow`), dataclass frozen/slotted shape, and
  three tests against a real Temporal server (`localhost:7233` on host,
  `PLATFORM_TEST_TEMPORAL_TARGET` override for the `c2-test-runner` container, matching
  the established `PLATFORM_TEST_MONGO_HOST`/`PLATFORM_TEST_NEO4J_HOST` convention): the
  mutex-serialization test that caught the concurrency bug above (stable across 5 runs
  post-fix), a `generation_changed` signal → `execution_state` query observability test,
  and a resume-after-crash test that starts a workflow under one real `Worker`, submits a
  turn and a signal, fully tears that `Worker` down (`async with` exits — no Python object
  survives), stands up a second, independent `Worker` for the same task queue, and
  confirms a fresh query/update against the still-running workflow execution sees the
  pre-crash state and can still make progress — a direct proof that Temporal's replay
  mechanism (not any persistence code of ours) is what survives a worker-process restart.
- `test_order_discovery_worker.py` (new, 7 tests) — exact workflow/activity name
  registration (mirrors `test_return_workflow_worker.py`), invalid-task-queue rejection
  before worker creation (parametrized, 6 cases).

**Process note.** The `c2-test-runner` container (created ~2 hours prior in this session)
has no live bind mount — it is a frozen `COPY` snapshot from creation time. Running the
full suite against it without first `docker cp`-ing the current working tree silently
re-verified stale, pre-Commit-3 code (confirmed by grepping the in-container
`coordinator.py` for `workflow_id` and finding nothing). Caught before drawing any
conclusions from those runs; fixed by `docker cp backend/src backend/tests` into the
container before every real-infra run for the remainder of this commit. Worth carrying
into Commit 3's follow-up work: this container has no automatic resync, so any future
session reusing it must re-copy before trusting its results.

**Gate receipts.**
- `ruff format --check` / `ruff check` on the exact 12-file Commit 3 change set: clean.
- `mypy src`: 47 errors in 16 files — **unchanged** from the Commit 2 baseline.
- `python -m compileall -q` / `python -c "import return_platform.main"`: clean.
- `pytest tests/ -q` inside the freshly-synced `c2-test-runner` container (real Mongo +
  real Neo4j + real Temporal, `PLATFORM_TEST_MONGO_HOST=mongodb`
  `PLATFORM_TEST_NEO4J_HOST=neo4j` `PLATFORM_TEST_SQLSERVER_HOST=sqlserver`
  `PLATFORM_TEST_TEMPORAL_TARGET=temporal:7233`), excluding `test_order_agent_rest.py`
  and the same three container-artifact-only files flagged in Commit 2's ledger entry:
  **1578 passed, 2 skipped, 0 failed**, 99 errors — all 99 the same pre-existing
  `NVIDIA_API_KEY`/`GOOGLE_API_KEY` fixture gap, zero relation to this commit's diff.
- New test files individually: `test_order_discovery_workflow.py` (6/6, mutex test
  stable across 5 consecutive runs), `test_order_discovery_worker.py` (7/7).

## Phase 7 / Wave C2, Commit 4 — interrupt-based CLARIFY + workflow lifecycle (this slice)

**Scope.** Task #80, the two pieces Commit 3 deferred. Designed inline rather than via a
Plan-agent pass (the agent was cut off by a session limit mid-design), verifying both
third-party APIs directly against the installed packages instead of assuming them.

**A. CLARIFY is now a real LangGraph `interrupt()`/`Command(resume=...)` pause.**
Previously CLARIFY ended the turn and the associate's follow-up became a brand-new
reasoning attempt on a brand-new thread, discarding everything the paused attempt had
already established. Now:
- `graph_nodes.make_clarify_node` calls `interrupt(<serialized clarifying question>)`
  after its existing guards. **Verified from `langgraph.types.interrupt`'s own source:
  on resume LangGraph re-executes the node from its first line**, so everything above
  the `interrupt()` runs twice. That is safe here only because all of it is read-only
  (budget check, two guards, an evidence read) — noted in an inline comment so nothing
  side-effecting is ever added above that line.
- `clarify` is no longer terminal. New `graph.py::_route_after_clarify` sends a resumed
  clarify back to `decide` (a correction still routes exactly as before). Reaching the
  router at all means the pause is over — the interrupted pass never returns.
- New `clarification_exchanges` state field (added to `OrderAgentGraphState`, the
  checkpoint allowlist, and `AgentTurnContext`) records `{question, answer}` pairs so the
  resumed `decide` can see the answer and cannot re-ask the same question. Both halves are
  conversation text of the same sensitivity as `user_message`, already checkpointed.
- `coordinator.process_turn` gained `resume_thread_id`. When set it reuses the original
  turn's thread and passes `Command(resume=request.message)` **instead of** the initial
  state (re-sending a fresh initial state would clobber the paused checkpoint). It detects
  the pause via `__interrupt__` in the returned state and — critically — **skips
  `mark_terminal`**: a paused run is PENDING, not COMPLETED. Marking it terminal would
  stamp an expiry on the very checkpoint the answer needs and free `abandonment.py` to
  sweep a conversation that is merely waiting on a human. The turn is still committed, so
  the associate sees the question and the conversation version advances normally.
- `thread_ids.py`'s "a thread is one attempt, not one conversation" invariant is
  **preserved, not changed**: a resumed clarification is still one attempt, now spanning
  two HTTP requests.
- The workflow owns the pending pointer (`RunOrderDiscoveryTurnActivityInput.resume_thread_id`
  in, `AgentTurnResultPayload.pending_clarification_thread_id` out), consuming it exactly
  once and restoring it if the turn raises or errors, so a failed answer never strands a
  paused thread that no later turn can reach.

**B. `OrderDiscoveryWorkflow` no longer runs forever.** `run()`'s
`wait_condition(lambda: False)` became a loop handling the two costs of staying alive:
- **Unbounded history** — defers to Temporal's own server-side
  `workflow.info().is_continue_as_new_suggested()` (accounts for both event count and
  history size) rather than an arbitrary turn count, awaits `all_handlers_finished()` so
  an in-flight `submit_turn` is never severed, then `continue_as_new`s carrying the durable
  coordination state on the input so the reset is invisible to callers.
  `OrderDiscoveryWorkflowInput` gained defaulted carry-over fields for exactly this.
- **Abandoned conversations** — a 7-day idle timeout on the same `wait_condition`. Because
  its timeout is absolute rather than sliding, an `_idle_deadline_extended` flag (set by
  `submit_turn`/`generation_changed`) distinguishes "genuinely idle" from "activity
  happened mid-window", re-arming in the latter case. Ending the workflow destroys nothing
  durable: the conversation document and checkpoints remain, and `api/order_agent.py`
  transparently starts a fresh execution on the next turn.

**Tests (fast; all pass).** `test_order_agent_graph.py` — the old
`test_clarify_ends_the_turn_with_a_requested_input_response` asserted the now-removed
terminal behavior and was replaced by two tests: the graph suspends with the question as
the interrupt value and no `final_response`, and a `Command(resume=...)` on the same
thread continues to a real answer with the exchange recorded and visible to the resumed
`decide` (9/9). `test_order_discovery_workflow.py` — a new real-Temporal test proving the
pending pointer is set from the paused turn, handed to the next turn as `resume_thread_id`,
and consumed exactly once so a third turn does not re-resume it (7/7).

**Real finding, flagged not fixed (`task_f1fc6b63`).** Commit 2's ledger entry claims
`ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST` is what "`CheckpointRedactor` enforces against every
checkpoint write" and that an unlisted field "fails closed". **That is not true in the
code**: `CheckpointRedactor` is constructed nowhere in `src/` and `enforce()` is never
called from `SystemStoreCheckpointSaver` or the coordinator — the allowlist is only
exercised by an isolated unit test comparing it to the TypedDict's keys. Wiring it as-is
would also immediately fail, because the graph's own transient routing key `_corrected` is
in neither the TypedDict nor the allowlist. Left as its own task rather than silently
expanding this commit; the Commit 2 claim needs correcting there too.

**Not done (unchanged scope boundaries).** The 7-day idle timeout and the continue-as-new
path have no automated test — both need Temporal's time-skipping test environment
(`WorkflowEnvironment.start_time_skipping()`, which downloads a test-server binary) rather
than the real server this suite uses. The mechanisms are implemented and type-check, but
are **not** behaviourally proven; that is the first thing to add when time-skipping is
available. Still no LLM-driven end-to-end proof, for the same missing-API-key reason as
Commit 3.

**Gate receipts.** `ruff format --check`/`ruff check` on the 9-file change set: clean.
`mypy src`: 47 errors in 16 files — unchanged baseline (a transient 48th,
`Command` missing type arguments, was fixed to `Command[Any]`). `compileall`: clean.
Fast suites: `test_order_agent_graph.py` 9/9, `test_order_agent_graph_state.py` 3/3,
`test_order_discovery_workflow.py` 7/7, `test_order_discovery_worker.py` 7/7.
Full real-infra suite (batched at the end of the session at the user's instruction, run
inside a freshly re-synced `c2-test-runner` against real Mongo/Neo4j/SQL Server/Temporal,
same four exclusions as Commit 3): **1580 passed, 2 skipped, 0 failed**, 99 errors — all
99 the same pre-existing `NVIDIA_API_KEY`/`GOOGLE_API_KEY` fixture gap, unchanged in count
from Commit 3. The +2 over Commit 3's 1578 are this commit's net-new tests.

## Wave C3.1 — Graph Schema Analyzer: persistent independent module (this slice)

**Scope.** Phase 9 of C3: the module's skeleton and persistence, built from scratch
(nothing existed under `src/return_platform/graph_schema_analyzer/`). Deliberately does
**not** include discovery, AI reasoning, mutations, validation, or approval — those are
C3.2/C3.3. Their routes are absent rather than stubbed, so OpenAPI never advertises an
endpoint that does nothing.

**What landed.**
- `domain/` — `AnalysisSession` (explicit transition table; illegal jumps like
  DRAFT→APPROVED raise rather than being emergently possible), `SourceSchemaSnapshot`
  (immutable, content-addressed), `Clarification`, typed errors. Pure: an architecture
  test asserts it imports no port, no persistence, no framework.
- `ports/` — `SourceDiscoveryPort`, `SchemaReasoningPort`, `GraphTargetPort`,
  `PersistencePort`, `AnalyzerAuditPort`. **No `adapters/` package**, per §2.7.
- `persistence/` — one repository per entity family, each with the write discipline that
  entity actually needs: compare-and-set on `(analysis_id, version)` for sessions
  (multiple analysts edit one session over days, so a lost update is realistic, not
  theoretical); idempotent content-addressed upsert for snapshots (safe *because*
  they're immutable — there is no prior state to destroy); state-machine-guarded upsert
  for clarifications. `build_system_store_persistence()` is a typed factory so **mypy**
  proves port conformance, rather than leaving it to a runtime `isinstance` that only
  checks method names exist (design doc's three-layer conformance table).
- `api/` — versionless `/api/graph-schema` (§9.3), with wire models separate from domain
  models: `SnapshotView` exposes `sample_classification` so an operator can audit how
  samples were handled, but deliberately **not** `samples_ref`, which is an internal
  pointer into an encrypted structure.
- `module.py` — **the codebase's first `module.py`**, which is load-bearing beyond this
  module: `tests/platform/test_no_module_cross_imports.py` treats a package as "migrated"
  exactly when it has one, so this file switched that architecture test on for
  `graph_schema_analyzer` automatically, and brought it under
  `test_no_adapters_package_outside_bootstrap`. Both verified passing.
- `config/platform/system_store.yaml` — four new structures: `analysis_sessions`,
  `source_snapshots` (with a `sample_expires_at` TTL that expires the *sample reference*
  while plaintext dataset metadata is retained indefinitely), `source_samples`
  (`encrypted: true` + TTL), `clarifications`. Verified through the real manifest loader.

**Section 13.6 enforced in the constructor, not the repository.** `SampleClassification`
(`NONE`/`REDACTED`/`ENCRYPTED`) is validated inside `SourceSchemaSnapshot`'s own
validator, so a snapshot that misrepresents how its samples were handled is impossible to
*hold*, not merely impossible to save — there is no path that builds one and decides
later. `ENCRYPTED` without `sample_expires_at` is rejected outright: raw retained samples
with no TTL are an indefinite liability the moment a key leaks.

**Platform contract extended (design-sanctioned, not a redesign).**
`ModuleRuntimeContext` gained `system_store: SystemStore | None`, and
`bootstrap/context.py`'s `RuntimeContext` gained the matching defaulted field. The
Protocol's own docstring had always said system_store/secrets/redactor/audit would be
added "once Phase 3 introduces those platform packages — extending this Protocol, not
redesigning it"; the analyzer is the first module with durable per-entity persistence, so
this is the first of them to land. Defaulted to `None` so every existing construction site
stays valid. Note this is *not* the R2a coupling: the system store is a platform service,
and a module still reaches another **module's** services only through `capabilities`.
`CapabilityName` was deliberately left alone — it is a closed enum of cross-module
capabilities, and persistence is not one.

**Real bug found in my own architecture test, worth recording because the same sharp edge
exists elsewhere.** The first version used `imported.startswith(FORBIDDEN_PREFIXES)` with
`"return_platform.graph"` in the list — which prefix-matches
`"return_platform.graph_schema_analyzer"`, so the module flagged all 40 of its own
internal imports as violations of itself. Fixed with package-boundary matching
(`imported == name or imported.startswith(f"{name}.")`). **`tests/platform/test_layering.py`
carries the identical `return_platform.graph` / `return_platform.graph_schema_analyzer`
pair with a bare `startswith`** — harmless there today only because `platform/*` imports
neither, so it is a latent trap rather than a live bug. Not fixed here (different test,
different slice); noted so it is not rediscovered the hard way.

**Not wired into the composition root.** `module.py` is complete and type-checks, but the
module is not in `main.py`'s `module_ids` and its router is not mounted, so the API is
unreachable at runtime. Mounting is explicitly the caller's job (`bootstrap/lifespan.py`'s
own docstring puts router mounting in "steps 13–15 … supplied by the caller"), and doing
it properly also requires re-introducing a `SystemStore` into the FastAPI process — which
Commit 3 removed once order-agent stopped needing one there. Called out in the module
README and deferred to the start of C3.2 rather than half-wired.

**Gate receipts.** `ruff format --check`/`ruff check` on the 25-file change set: clean.
`mypy src`: 47 errors in 16 files — **unchanged baseline** with 21 new files checked (a
transient 48th was a real bug: the API layer used `Principal.principal_id`, which does not
exist; the field is `subject`). `python -c "import return_platform.main"`: clean.
`tests/graph_schema_analyzer/` 17/17; `tests/platform/` architecture + manifest-loader
tests pass. The full real-infra suite was **not** re-run for this slice — no runtime path
changed, but it is owed before C3.2 lands on top.

## Wave C3.2 — discovery, prompt framing, and the analyzer reasoning loop (this slice)

Three commits: composition-root wiring (`3212bb6`), discovery + prompt framing (`7b8f76a`),
and the reasoning graph.

**Wiring (owed from C3.1).** `main.py` now bootstraps a `SystemStore` in the FastAPI
process — re-introduced after Commit 3 removed it once the order agent stopped needing one
— builds the analyzer's persistence onto `app.state`, and mounts the router, so
`/api/graph-schema` is live. Bootstrap failure degrades to an explicit `UNAVAILABLE`
state and a 503 from the routes rather than blocking startup: the analyzer is an operator
tool, and the return flow must keep serving if schema analysis cannot persist. The manifest
bootstrap moved from `dynamic_knowledge/integration/reasoning_bootstrap.py` to
`bootstrap/system_store.py` and lost the "reasoning" name — it always loaded the whole
manifest, and with a second unrelated consumer, leaving it in a business module would make
the analyzer's composition depend on `dynamic_knowledge`. The API layer is typed against
`PersistencePort`, not the concrete repository bundle (it had been reaching into
`persistence.clarifications.load`, which defeats the port).

**Module activation deliberately not done.** The router is mounted conventionally in
`create_app` rather than through `module_ids`. `bootstrap/lifespan.py`'s own docstring puts
router mounting in "steps 13–15 … supplied by the caller", and mounting during lifespan
would mutate `app.routes` after the OpenAPI schema is built. Deferred until the kernel owns
mounting; `module.py` already satisfies the contract.

**Discovery separates access from retention.** `SamplingPolicy` splits "how many rows may
be read" from "what may be kept" — easy to conflate, and conflating them is how a source
that permits reading for reasoning silently starts persisting. Default is read nothing,
keep nothing. A hard `MAX_PERMITTED_SAMPLE_ROWS` ceiling refuses (rather than clamps) a
policy asking for more: a request for a million rows is an exfiltration shape, not an
analysis. `DiscoveryService` enforces §13.6's ordering — read, build always-plaintext
metadata, classify, redact, seal, *then* build the snapshot — so a snapshot can never
exist claiming a classification that was not applied. Two decisions worth recording: a
mixed analysis reports the **weakest** guarantee it can honestly make (one raw-retaining
source makes the shared document raw; claiming REDACTED would be false), and the
**shortest** retention period governs (samples share one document, so honouring the
longest would silently extend another source's retention).

**Prompt framing treats labelling as insufficient.** The six blocks (§10.5) are built by
`application/prompt_context.py`. Announcing block 5 as untrusted is necessary but not
sufficient: content that can emit its own `=== BLOCK 1: SYSTEM POLICY ===` line would
append trusted-looking text after it, and prompt structure is only a boundary if content
cannot forge the marker. So every block's content is scanned and forged delimiters
neutralised — **including source metadata**, since a column name also originates outside
the platform. Tested adversarially with a sample row and a column name that each try it,
plus a case/spacing variant.

**Reasoning loop.** `reasoning/` compiles the §14.4 graph and stops at
`READY_FOR_APPROVAL` — never build/activate/drain/retire/DDL. A test AST-scans the whole
package for those names; weaker than proving it never happens, but it catches the realistic
mistake (someone adding a convenient `request_build()` instead of routing through
`ApprovalService`). Clarification is a real `interrupt()` whose payload carries references
and the question only. Every loop is bounded by `limits.py`, and exhausting a budget routes
to `NEEDS_HUMAN_REVIEW` with a reason rather than raising — an analysis needing a human is
a normal outcome. State carries no raw samples, only `source_snapshot_id`/
`source_schema_hash`; `ANALYZER_CHECKPOINT_ALLOWLIST` and the TypedDict keys are asserted
identical.

**Two real bugs the tests caught, both mine.**
1. `state.get("proposal", {}).get(...)` returned `None` and crashed, because `.get` only
   applies its default when the key is *absent* — and both the revise and clarify paths set
   `proposal` explicitly to `None`. Fixed to `(state.get("proposal") or {})` at both sites.
2. **A design flaw, not a typo:** `IDENTIFY_GAPS` runs before `PROPOSE_SCHEMA`, but its gap
   signal is the model's own `open_questions`, which do not exist until it has proposed. On
   a first pass it always saw none, so **the clarification branch was unreachable** — the
   graph completed instead of suspending. Fixed with an `open_questions` edge from
   `PROPOSE_SCHEMA` back to `IDENTIFY_GAPS`: a proposal arriving with open questions means
   the model guessed, and validating a guess burns a validation attempt on something a
   human could answer. This is a deliberate departure from the design doc's diagram, which
   draws PROPOSE straight into VALIDATE; documented in `routing.route_after_propose_schema`
   and in the graph's own docstring.

**Gate receipts.** `ruff format --check`/`ruff check` clean. `mypy src`: 47 errors in 16
files — **unchanged baseline**, now across 448 source files (a transient failure was
LangGraph's `add_node` overloads rejecting a precise `Callable` node alias; resolved with
`NodeFn = Any`, the same accommodation `dynamic_knowledge/order_agent/graph_nodes.py`
already makes). `tests/graph_schema_analyzer/` 56/56, plus the platform architecture tests.
The full real-infra suite has **not** been re-run since C3.1 — no runtime path outside the
analyzer changed, but it is owed before C3.3.

## Wave C3.3 — typed mutations, validation, and approval (this slice)

Two commits: the mutation domain + services (`35d81a5`), then persistence, API, and the
end-to-end flow.

**"No model-authored executable statement reaches a database" is now structural.** All 17
commands from §10.4 are closed pydantic models with `extra="forbid"` and only enumerated,
pattern-constrained identifier fields. There is deliberately no `statement`/`sql`/`cypher`/
`expression` field anywhere in `domain/mutation.py`, and a test asserts that absence — a
command that *could* carry an executable string would make the guarantee a policy someone
has to remember rather than a property of the type system. Identifiers are regex-bounded
for the same reason: `Order) DETACH DELETE n //` is not a label, and rejecting it at parse
time beats escaping it at compile time. `TransformationKind` is a closed enum because a
transformation is the most tempting place to accept arbitrary code. Enforcement lands at
the narrowest point available — the API's request model — so a smuggled command never
reaches analyzer code at all.

**The draft state machine enforces §10.4's core rule itself.** Any mutation returns a draft
to DRAFT and clears its `validation_result_id`, *including* from APPROVED: a validation
result describes one specific shape, and building on a stale one is the failure the whole
machine exists to prevent. Editing an approved schema is legitimate; silently keeping the
approval is not. Approval is bound to a specific `revision_id` **and**
`validation_result_id`, not just to a draft, and a decision is final — the audit question
is "what did this person decide", which a mutable answer cannot serve.

**Validation: 14 checks, and the design doc's count is wrong.** §10.4 says "all 13 must
pass" then enumerates fourteen. Implemented the names — dropping one to match a prose
number would drop an explicitly named safety check. Two behaviours worth recording: every
check always runs (an analyst fixing one problem should see all of them, not discover the
next one on the next attempt, and each attempt costs a reasoning-loop budget slot), and an
**unevaluable check is a failure, not a skip** — an unreachable graph target records an
ERROR, and `ValidationResult.passed` additionally requires every required check to appear
in `checks_run`, so "we could not tell" can never read as "it is fine". The validate
endpoint 503s without a graph target rather than running partially.

**Crash ordering in `DraftService`.** No cross-collection transaction is available, so the
order is: append the revision (insert-only, unique on `(draft_id, sequence)`), *then*
advance the draft. A crash between them leaves an orphan revision whose sequence exceeds
`current_revision` — detectable, idempotent to retry, and safe, because the draft still
describes a shape that was really built. The reverse order would point a draft at a
revision that does not exist, which is unrecoverable history loss.

**Two real bugs caught by tests, both mine.**
1. **Aliasing in `apply_mutations`.** It shallow-copied entity dicts, so the nested
   `properties` dict stayed shared with the input shape and `AddProperty` mutated the
   caller's supposedly-immutable shape in place. The module's whole claim is purity. Found
   because a diff test saw no change between before and after — both had been changed.
   Fixed with `deepcopy`.
2. **Extending `PersistencePort` silently 503'd the older test double.** `resolve_persistence`
   guards with a runtime `isinstance` against the Protocol, so a double missing any new
   method makes every route unavailable. That is correct fail-closed behaviour and is
   exactly how the drift surfaced; resolved by making one complete double rather than
   several partial ones. Worth knowing before the real bootstrap adapters land: **any
   adapter that has not caught up with the port will 503, not fail at import.**

Also removed a `_NullTarget` stand-in mypy correctly rejected. Rather than fudge the type,
`DraftService.validation` became optional — only `validate()` needs it, and a stand-in that
silently approves everything is precisely the thing that turns a missing target into a
passing validation.

**Not done (C3.3 scope boundary).** The reasoning graph's `APPLY_TYPED_MUTATION` node and
the USER_REVIEW "modification" branch are still absent — the mutation machinery they would
drive now exists, but wiring the model into it is a separate step and belongs with the AI
adapter, which has no production binding yet either.

**Gate receipts.** `ruff format --check`/`ruff check` clean. `mypy src`: 47 errors in 16
files — **unchanged baseline**, now across 459 source files.
`tests/graph_schema_analyzer/` 91/91 plus the platform architecture tests (94 total).
`import return_platform.main` clean. Manifest additions (`schema_revisions`,
`validation_results`, `schema_approvals`) verified through the real loader. The full
real-infra suite has **not** run since C3.1 and is now overdue.

## Post-C3 — analyzer port adapters + the deferred real-infra gate (this slice)

**Two production port bindings.** The analyzer's ports had no implementations, so the
module was unreachable in production regardless of being wired into startup.

- `bootstrap/adapters/analyzer_source_adapter.py` — `SourceDiscoveryPort` over real Mongo.
  **Mongo has no declared schema**, so field metadata is *inferred* from observed
  documents, and the adapter is explicit about the limits of that: a field absent from
  every sampled document is invisible, and a field whose sampled values disagree is
  reported as `mixed` rather than guessed. That matters because the analyzer's
  TYPE_COMPATIBILITY check treats declared types as fact — a confident wrong answer here
  becomes a silent coercion at sync time. Sampling is bounded twice (caller limit, then a
  module ceiling) so discovery cannot become a bulk export, and `_id` is stripped as
  storage bookkeeping that is also not JSON-serialisable.
- `bootstrap/adapters/analyzer_graph_target_adapter.py` — `GraphTargetPort` over Neo4j.
  **This is the compiler §10.4 means**: the only place a model-derived structure becomes
  Cypher. Identifiers are re-validated here rather than trusted from the mutation layer —
  a compiler that assumes its input was validated upstream is one refactor away from not
  being — and anything failing the pattern *raises* rather than being escaped, because an
  escaping bug is silent and a refusal is not. Only `CREATE INDEX`/`CREATE CONSTRAINT` are
  emitted; there is no path that writes to a source system. `validate_schema` uses Neo4j's
  own `EXPLAIN` so the database decides validity rather than this adapter re-implementing
  Cypher's grammar and drifting from it. `request_build` deliberately raises
  `NotImplementedError` — the generation lifecycle is C4, and a half-build would be worse
  than a refusal.

Both are bound in `main.py` independently: an operator with Mongo but no Neo4j gets
discovery and a 503 on validation, rather than a wholly dead module.

**`compile_graph_ddl` is pure and adversarially tested** (16 cases) — six injection-shaped
labels, a property name carrying a Cypher fragment, determinism, and the Mongo inference
edge cases (bool-before-int, mixed types, empty sample).

**Container contamination found — and it invalidated a green result.** The `c2-test-runner`
container is shared mutable state, and `docker cp` *merges* rather than replaces. A
concurrent session working the `CheckpointRedactor` task (`task_f1fc6b63`) had synced its
own files into the same container, so the first full run — reported as 1669 passed / 2
failed, then 1671 passed / 0 failed on a re-run — was executing a **mixture of two
sessions' code**, including a `tests/reasoning/test_checkpoint_allowlist_fails_closed.py`
that does not exist in this working tree. The 2-then-0 failure discrepancy is explained by
the other session writing mid-run. Neither number was attributable to this tree. Resolved
by `rm -rf`-ing the container's `src`/`tests`/`config` and re-copying from a clean tree
before re-running. **Carry forward: always wipe before sync, never merge, and never trust
a container result while another session is active against the same repo.**

**Gate receipts (clean tree, contamination removed).** `ruff format --check`/`ruff check`
clean. `mypy src`: 47 errors in 16 files — **unchanged baseline**, across 461 source files.
`import return_platform.main` clean. `tests/graph_schema_analyzer/` 107/107 locally.
Full real-infra suite in the wiped-and-resynced `c2-test-runner` (real Mongo/Neo4j/SQL
Server/Temporal, same four exclusions as prior slices): **1687 passed, 2 skipped, 0
failed, 99 errors** — 99 being exactly the long-standing `NVIDIA_API_KEY`/`GOOGLE_API_KEY`
fixture gap, unchanged since Commit 3. This is the first full-suite run since C3.1 and it
closes that three-slice gap. The +107 over Commit 4's 1580 is this wave's new tests.

## Correction — the "99 pre-existing errors" baseline was never a real blocker

**Every gate receipt from Phase 8 onward reported ~99 errors as an accepted, pre-existing
condition caused by `NVIDIA_API_KEY`/`GOOGLE_API_KEY` being rotated out by `fbfcf05`. That
framing was wrong, and it cost real work.** It blocked the AI adapter (`task_971021e8`),
was cited as the reason there could be no LLM-driven end-to-end test in Commits 3 and 4,
and was carried forward unexamined across eight commits.

The 99 errors are entirely an **invocation** problem, not a code or credential problem:

- **95** — `tests/conftest.py`'s `test_settings` fixture calls `_required_environment_variable`
  for those two keys purely to populate `Settings` fields. None of the affected tests makes
  a provider call. **Any non-empty placeholder satisfies them**; passing
  `NVIDIA_API_KEY=placeholder-not-a-real-key GOOGLE_API_KEY=placeholder-not-a-real-key`
  takes the suite from 99 errors to 4.
- **4** — `tests/source_connectors/test_sqlserver_connector_docker.py` inherits
  `PLATFORM_SQLSERVER_PORT=14330` from `.env`, which is the *host-published* port. Inside
  the compose network SQL Server listens on **1433**. DNS resolves and the port is open;
  only the number was wrong. Overriding it takes 4 to 0.

**And real credentials were never required to exercise the AI path anyway.** The platform
already ships `ManualFileProvider`: it writes the exact request a model would receive to
`.manual_llm/requests/` and waits for a human-authored reply in `.manual_llm/responses/`,
`scripts/manual_llm_responder.py` is its interactive companion, and
`ORDER_AGENT_REASONING_V1` **already lists `MANUAL` in `allowedProviders`**. Settings refuse
MANUAL in production and the provider itself gates on `environment in {development, test}`,
so it is structurally undeployable.

`tests/test_manual_provider_reasoning_e2e.py` (5 tests) now closes the "no LLM-driven
end-to-end test" gap that Commits 3 and 4 both recorded. It drives the genuine production
path — `build_routes` → `AIRoutePool.candidates` → tier/provider gating → rate-limit
acquisition → `ManualFileProvider.generate` → response-schema parse → typed `AgentAction` —
with the model's reply supplied by a background task standing in for the human. It proves a
well-formed reply is carried through and a malformed one is rejected rather than coerced;
it does **not** prove anything about real model behaviour, which is stated in the module
docstring so the coverage is not overread.

Two implementation notes worth keeping: `ManualFileProvider`'s directory is **not**
configurable — `routing.py` constructs it with no `base_dir`, so it is always `.manual_llm`
relative to the process CWD, and a test must patch the module global. And the retry path
needs a responder that answers *every* request, not just the first, or each retry waits out
the provider timeout (42s → 5s for that file).

`scripts/dev/run_real_infra_suite.sh` now encodes the correct invocation — including the
wipe-before-`docker cp` step — so none of this has to be rediscovered.

**Gate receipt: the suite is green for the first time.** With both invocation fixes, against
real Mongo/Neo4j/SQL Server/Temporal: **1795 passed, 2 skipped, 0 failed, 0 errors** (481s).
Compare with the run earlier the same day: 1687 passed / 99 errors. Nothing about the code
changed between them. `mypy src` unchanged at 47 errors in 16 files; analyzer suite 107/107.

**Use this number, not the old baseline, as the reference point.** Any future report of
"~99 pre-existing errors" means the suite was invoked the old way, not that something
regressed.

## ReturnWorkflow.complete_stage mutex race — fixed (this slice)

**A real concurrency defect in shipped Phase 6 code, now reproduced and fixed.** It is the
same `wait_condition` batch-release bug found in `OrderDiscoveryWorkflow.submit_turn`
during Wave C2 Commit 3, flagged then as `task_92c35ace` and left because it needed its own
test and commit.

`complete_stage` waited once on `self._persistence_ready and not
self._transition_in_progress`, then set the flag. `wait_condition` never resolves
synchronously — it registers a future and the SDK evaluates all pending conditions in one
batch pass — so two handlers parked on that predicate are released **together** when
`run()` sets `_persistence_ready = True` after the initialize activity. Neither sees the
other take the flag.

**Worse here than in the order agent**: `self._state = next_state` happens only *after* the
transition activity returns, so both handlers read the same `previous_state`, compute a
`next_state` from it, and each persist a transition against the authoritative session
record. Reproduced against a real Temporal server:
`max_concurrent_transitions == 2`. Fixed with the same re-check loop, and the test was
confirmed to discriminate — it fails with the loop removed and passes with it restored.

**Second, independent defect found while building the test (`task_bd3a4652`, not fixed
here).** `ReturnWorkflowTransitionError` is a plain `RuntimeError`, and Temporal treats a
non-`ApplicationError` raised from an update handler as a **workflow task failure**, not an
update failure — so it retries forever. A single out-of-order or conflicting
`complete_stage` command therefore wedges the entire return session permanently; later
updates fail with "Workflow Task in failed state". This is reachable from any caller
sending a stale or duplicate command, and the existing unit tests miss it because they call
`advance_return_workflow` as a pure function rather than through a running workflow. The
first version of this test used two *different* commands and hung on exactly this;
switching to one shared command (which `advance_return_workflow` deduplicates to a no-op)
isolates the race without touching the wedging path.

**Environment note.** Those wedged executions accumulated across several hung runs and
loaded the Temporal Postgres store enough that the server went unhealthy and even
`list_workflows` timed out; recovery needed a `docker restart` of the Temporal container.
Worth knowing before writing further real-Temporal negative tests: a test that provokes a
workflow-task failure leaves a permanently-retrying execution behind.

**Gate receipts.** `ruff format --check`/`ruff check` clean. `tests/test_return_workflow_concurrency.py`
2/2 (and 2/2 failing with the fix reverted, confirming the test is real).
`test_return_workflow.py` + `test_return_workflow_worker.py` + `test_order_discovery_workflow.py`:
29/29, no regression. **Not** re-run: the full real-infra suite — the Temporal restart
happened mid-slice and a full pass is owed before the next change lands on top.

## Next READY slice

**Wave C3 is complete** (C3.1/C3.2/C3.3). Next is **C4 — Phase 12: graph generation
lifecycle** (build, catch-up, deep validation, READY_FOR_ACTIVATION, atomic activation,
read/write/session leases, DRAINING, RETIRED, failure rollback, rebuild trigger), after
which Wave D (Phases 13–16) opens.

`SourceDiscoveryPort` and `GraphTargetPort` have production bindings, and the analyzer's
persistence now has a real-Mongo proof (`tests/graph_schema_analyzer/test_persistence_real_infra.py`,
9 tests): samples round-trip through the encrypted structure, the raw document contains no
business values, **the store itself** refuses a plaintext write to `source_samples`, an
encrypted structure hands out no raw collection, `(draft_id, sequence)` genuinely raises
`DuplicateKeyError` on a second insert, and draft compare-and-set rejects a stale write.
Its fixture is module-scoped — bootstrapping the full manifest per test cost 182s for 9
tests versus 13.7s shared; safe only because every test allocates uuid-based ids, which is
noted in the fixture so it goes back to function scope if that stops holding.

Still owed, in rough priority order:

1. **`SchemaReasoningPort` has no adapter — the analyzer cannot propose a schema in
   production.** Spawned as `task_971021e8`. Detail worth keeping:
   `AIGatewayService.evaluate` is **not** usable here — its `_parse_response` requires
   exactly `{decision, explanation, confidenceMillionths}`, so it cannot carry a
   `SchemaProposal`. The real structured-output path is
   `dynamic_knowledge/integration/model_gateway.py`'s `RoutePoolReasoningModelGateway`
   (385 lines, of which only ~33 touch order-agent types). The design mandates exactly one
   AI execution path, so the options are duplicate ~350 lines (violates that and will
   drift), write an adapter without failover (a silent reliability regression), or extract
   the generic machinery (correct).
   **The blocker recorded on that task is now removed** — see the correction above.
   `tests/test_ai_route_balancing_design.py` passes with placeholder keys, so the failover
   and tier-escalation regression coverage the extraction needs is available, and
   `tests/test_manual_provider_reasoning_e2e.py` gives a keyless way to exercise the real
   invocation path end to end. The extraction is ready to do.
2. The reasoning graph's `APPLY_TYPED_MUTATION` node and the USER_REVIEW modification
   branch (the mutation machinery they would drive now exists).
3. Time-skipping coverage for Wave C2 Commit 4's idle-timeout and continue-as-new paths.
4. `task_f1fc6b63` (concurrent session): wire `CheckpointRedactor` into real checkpoint
   writes and correct Commit 2's untrue ledger claim about it.

**Done since this list was first written:** `task_92c35ace` (the
`ReturnWorkflow.complete_stage` mutex race) — fixed and verified in `99101c7`.
`task_bd3a4652` (the rejected-command wedge) — fixed and verified below. The
`NVIDIA_API_KEY`/`GOOGLE_API_KEY` item is also resolved: see the correction above; the keys
were never actually required.

## The static gate passes, and one real bug was hiding behind a blind assertion

Status: DONE. `ruff check .` and `ruff format --check .` both pass across all 735 backend
files for the first time in this branch's recorded history. Two commits: `cb7928f`
(mechanical) and `6a5d942` (judgement).

### Why this mattered more than lint usually does

The header of this ledger carried a caveat for eleven slices: every "green" reported here
was the *changed-files* gate, not the one CI runs. A gate nobody can run and believe is not
a gate. `scripts/linux/03_run_backend_quality.sh` reported **246 errors and 86 unformatted
files**, all pre-existing, concentrated in Phase-2 `configuration/` code — enough noise that
a genuine new finding would have been invisible in it.

**`cb7928f` — mechanical, committed on its own so the churn stays reviewable.** 244
auto-fixes, 77 files reformatted. Mostly modernisation the tree had drifted from
(`typing.Optional`/`typing.List` → PEP 604/585, import ordering) plus eleven unused imports,
each checked individually first — none were `__init__` re-exports, which ruff would have
removed silently.

**`6a5d942` — the 21 that needed judgement.** Three `raise ... from`, two RUF005 unpackings,
a dead `original_construct` assignment, a mid-file import, a raw-string `match=` pattern,
unused unpacked variables. And two `B017` blind `pytest.raises(Exception)` assertions, one of
which was covering a real defect.

### The defect: a well-formed cursor whose value is the string "None"

`test_sync_records_failure_status_when_a_write_raises` **never reached a write.** Its
customer fixture omitted `updatedAt` — the configured incremental cursor field — so the run
died in `capture_high_watermark`, and `pytest.raises(Exception)` swallowed the difference.
The test asserted a FAILED run status for a failure with nothing to do with the fence it
claimed to exercise. It had been passing that way for as long as it has existed.

Underneath it: for a non-empty collection whose newest document has no cursor value,
`_encode_field_value(None)` returns the *string* `"None"`, which is a perfectly well-formed
`SourceCursor`. Nothing rejects it. The run continues and dies much later in
`_field_datetime_bounds` with a bare `ValueError: Invalid isoformat string: 'None'` that
names neither the source nor the field.

`capture_high_watermark` now fails closed where it can still name both. **Deliberately not
defaulted:** substituting `now()` silently skips every record, and the epoch silently
rescans the collection. This is the same family as the string-cursor finding from the Wave C
end-to-end run — where seeding a cursor field as an ISO string made `scan` return zero rows
while `capture_high_watermark` reported a plausible watermark, producing a silent empty
build. Both are cursor values that are *shaped* right and *mean* nothing.

The other `B017`: `test_runtime_snapshot_is_final_immutable_output` now asserts pydantic's
`frozen_instance` error. The blind form would have gone green if `PlatformConfig(...)` itself
started raising — e.g. on gaining a required field — leaving immutability untested while
the test still passed.

`test_pinned_release_resolves_after_handle_recreation` constructed a `handle1` it never
touched. It now asserts handle1's cache stays empty, which is what makes it a recreation
test rather than a cache-hit test.

### A verification hazard worth knowing about

This clone's fetch refspec was pinned to a single unrelated branch
(`+refs/heads/feat/v2-order-discovery-integration:...`), so `git fetch origin` never
refreshed `origin/refactor/unified-return-platform`. That ref sat at `31b2500` while the
remote was at `6a5d942`, and `git log origin/<branch>` reported stale data indefinitely —
the mechanism behind the branch-state surprise recorded earlier in this ledger. Restored to
`+refs/heads/*:refs/remotes/origin/*`; local, remote-tracking and remote now agree.

**Verification.** ruff check + format clean (735 files). mypy **47/16**, unchanged. Full
real-infra suite **1963 passed, 2 skipped** — one more than the previous run, from the new
connector regression test.

## Wave D: what remains

Wave D's *completion condition* — "backend exposes the four canonical API domains and all
are generated into OpenAPI" — is met, and nothing in D still blocks Wave E.

**Four D4 slices have landed since this list was written** (`5fdc17f`, `bc1baf7`,
`f7244fd`, `7c700e3`), and **item 2 is now closed**. Each has its own section below.

Worth stating once, because it shaped four slices: of the three duplicates Phase 16 named,
**two were not duplicates** (the artifact pair, the associate flow) and the third was a
state-machine defect rather than an API duplicate. The original inventory was written from
route paths rather than from the code behind them, and route paths were a poor proxy every
time. The genuine write-side problem — four routers recording workflow transitions with one
authorization check between them — was not on the list at all.

In rough order of blast radius:

1. **D3's configuration lifecycle — CLOSED** (`0612612`, `fec00b1`, `870e066`). There is
   one release lifecycle now: the graph one. Brief, with the measurements the choice was
   made against: **[CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md](CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md)**.
   Detail in the D3 section below.

   The mutation surface it was blocking is now merely **unbuilt, not blocked**. It would
   promote through `ConfigurationGraphRepository.promote_release`, the one path that
   enforces the lifecycle; what it still needs is a scope decision about which promotions
   belong on a versionless canonical API versus the Data Console's operator surface.

2. **D4's write consolidation — CLOSED** (`5fdc17f`, `bc1baf7`, `f7244fd`, `7c700e3`).
   All three Phase 16 duplicates are resolved; see the slice sections below.

   One inconsistency remains, and it is additive work rather than a duplicate:
   `POST /production-returns/{id}/events` contradicts design doc §9.1, which says progress
   is action-driven and callers submit *intent* rather than naming the transition. It
   survives because eight event types (`RECEIPT_CONFIRMED`, `LICENSE_PLATE_ASSIGNED`, the
   three `*_NOT_REQUIRED` waivers, `PRODUCT_DISPOSITION_COMPLETED`,
   `VENDOR_RECOVERY_REQUIRED`, `VENDOR_RECOVERY_COMPLETED`) have no action endpoint and it
   is their only path. Building those eight is what lets it retire.

   The canonical write surface is no longer blocked by anything.

3. **D4's remaining read domains — CLOSED as far as they are real.** Session, list,
   timeline, artifacts and evidence are canonical. Of the four the plan still named:
   *fulfillment* and *outbox events* need no endpoint — `/evidence` already returns
   `shippingInstructions`, `shipmentEvents`, `pickup` and `integrationCommands`, and a
   second path would duplicate them. *Warehouse* needs a new SQL read (bay assignment is in
   `platform.bay_assignment`; the repository has only `list_bay_candidates`, a planning
   read). *Support* and *conversation* were parked, and are **now closed** — see "The parked pair, unparked" below.
3a. **D3's promotion endpoint — CLOSED** (`d14e5c1`). `POST /api/config/releases/{id}/promote`.
   The open question was scope, not risk: which promotions belong on a versionless
   canonical API versus the Data Console's operator surface. **All of them.** Splitting the
   lifecycle would mean an operator takes a release to VALIDATED on one API and changes
   tools to publish it, and neither surface could tell a caller which transitions it was
   allowed to make. It delegates, like the reads — the console handler validates three
   behaviour domains, verifies unexpired runtime-validation receipts, requires
   `expected_head_revision`, and refreshes the process's active configuration; a canonical
   copy doing four of those five would be the second lifecycle D3 deleted. The response is
   re-scrubbed, because `GET /releases/{id}` redacts the same domain payloads and the same
   operator can call both.

3b. **E5's remaining operator actions — one built, four assessed** (`d14e5c1`). `POST
   /api/ai/interceptions/{id}/cancel` is the one with a backing mechanism, and it needed
   care: `store.cancel` returns *silently* when the record is not PENDING, because its
   other caller is the expiry sweep for which losing the race is normal. A handler trusting
   the absence of an exception would tell an operator they cancelled something that had in
   fact been answered, so the outcome is read back. Of the other four: Claim/Release need a
   new concept in the store, and the CAS on `answer` already makes a double answer safe
   rather than merely unlikely; Replay and Generate Candidate are hard to justify at all,
   since an interception exists *because* the AI could not be called and generating a
   candidate answer with an AI contradicts why the request was held.

4. **D3's remaining read domains — CLOSED** (`10a107b`). `/api/config` gained `sources` and
   `audit`, both delegating to the Data Console handlers rather than reimplementing them.
   The other four needed nothing: business config and integrations are already in
   `/runtime`'s snapshot, `modules` would return `[]` forever (`_kernel_module_registry` is
   empty by design), and `security` is not configuration — the role model is code, and
   publishing the role-to-capability table would let a UI reimplement authorization
   locally. All four are pinned by tests, including one that fails if the block explaining
   why is deleted.
5. **D2's API-process route wiring — CLOSED** (`04e5ec3`).
6. **D2's operator console — CLOSED** (`04e5ec3`).
7. **D1's untested claim — CLOSED** (`3d3b3a5`).

### The parked pair, unparked — and neither was the problem it looked like

**`/support`: the two stores are not competing implementations.** The park was right to
wait on "which is authoritative", but the question dissolves once you look at who writes
each. `support_cases` is raised *by the platform* — `orchestrator._fail` creates one when a
return flow fails, with a case type, a priority and an SLA; nothing human opens one.
`support_work_items` is opened *by a person* through the support workbench, and carries a
message thread and an eleven-state lifecycle. Different creators, different lifecycles,
neither derivable from the other. This is the artifact/evidence situation for the third
time: a shared word over different things.

So the response is two nullable fields rather than a merge. Both are at most one per
return — each collection has `sessionId` uniquely indexed, which is *why* `workItem` is
singular and not a list — and either can be absent. A return that never failed has no
case; a return support never touched has no work item.

The work-item service is resolved leniently. `api/return_support.py` 503s when its
dependencies are missing, which is right for a router whose every endpoint is a work item,
and wrong here: the case needs only the operational repository, and it is the record an
operator is most likely after, because the platform raised it when something broke.

**`/conversation` was genuinely unanswerable, for a much smaller reason than assumed.**
`returnSessionId` is stamped on the conversation when `submit_details` creates the return,
so the link was in the data — but only conversation-to-session, with no accessor and no
index for the reverse. Given a session there was no way to find its conversation. One
method (`AssociateConversationService.get_for_session`) and one sparse compound index
closed it. Sparse because most conversations never reach a return; not unique because a
session reached by two attempts is unusual but not corruption, and a unique index would
turn it into a write failure at the worst possible moment.

`null` is a successful answer on both endpoints, not a 404 — a SYSTEM-channel return has no
conversation and never will, and 404 would be indistinguishable from "no such return",
which the parent check already covers.

**Wave D is now closed with nothing parked.**

## Closing Wave D — D1's claim, D2's wiring and console, D3's remaining reads

Status: DONE (`3d3b3a5`, `04e5ec3`, `10a107b`).

### D1 — the shared-path claim is now a test (`3d3b3a5`)

`ai/README.md` argued that `AIGatewayService.evaluate` and
`StructuredOutputInvoker.invoke` share the route pool, configuration, safety guards,
breakers and limiters, differing only in response contract. Prose. Five tests decompose it:
the same injection is refused by both, the same payload ceiling rejects both, both draw
candidates from the *same* pool object — asserted by task id rather than call count, because
a count passes if one entry point asks twice and the other has its own pool — and they
diverge exactly where the README says, `REVIEW_REQUIRED` versus a raise.

`StructuredOutputInvoker` had no test constructing it anywhere before this.

The spy subclasses the real `AIRoutePool` rather than faking it, and takes `**kwargs`
because `evaluate` passes `force_provider` and `invoke` does not — pinning the exact
signature made the spy assert a calling convention it has no opinion about, and it broke
once for that reason before being loosened.

### D2 — one MANUAL provider, an operator surface, and a bridge nobody ran (`04e5ec3`)

**The two processes disagreed.** `build_routes(settings, interception_store=...)` decides
which MANUAL implementation the pool gets. The worker bootstrapped the SystemStore then
built routes and got the durable one; `main.py` built routes at line 582 and bootstrapped
~50 lines later, so it passed `None` and silently got `ManualFileProvider` — writing held
prompts to local disk, in a container whose disk does not persist. Bootstrap is hoisted
above route construction, every degrade path unchanged. It also removed a second bootstrap
that would have run in the same process: two owner ids contending for one lease.

**The operator surface** is two endpoints — unseal the request, then answer it — rather than
one enriched listing, so unsealing is a distinct auditable act. The payload can carry block
5 UNTRUSTED SOURCE SAMPLE, which is why it is sealed at rest; decrypting every pending
prompt to render a queue would defeat that. Gated on `ai.interception.act`, the narrower
capability, because you unseal in order to answer.

An absent store answers **503** here while the listing answers **`[]`**. That reads as an
inconsistency until you see it: "no pending interceptions" is true for a deployment that
never uses the manual path, but "here is interception X" has no truthful empty form, and a
404 would read as "already handled".

**The gap found between the two: `InterceptionResumeDispatcher` was never constructed
anywhere.** It existed and was tested; nothing ran it. An answered interception would sit at
`ANSWERED` forever and the waiting workflow would never be signalled — so shipping the
operator surface without noticing would have made it a lie. `workers/interception_resume.py`
runs it, bridging rather than delivering: answered interceptions become resume-command rows
and the existing reasoning resume worker turns those into Temporal signals, because
at-least-once is already solved there.

### D3 — sources and audit, and the four that needed nothing (`10a107b`)

Of the plan's six configuration domains, two needed building. Both delegate to the Data
Console handlers rather than reimplementing the probe fan-out or the audit query; a test
fails if either stops delegating.

The other four are the interesting part. Business config and integrations are already in
`/runtime`'s snapshot. `modules` would return `[]` forever. `security` is not configuration —
the role model is code, and publishing the role-to-capability table would let a UI
reimplement authorization locally, which the capability layer exists to prevent.

All four are pinned by tests, because "already served by `/runtime`" stops being true the
moment someone narrows what `/runtime` returns, and an endpoint that always answers nothing
is worse than an honest absence: it reads as "covered". One test fails if the block
explaining the reasoning is deleted, which is the realistic way reasoning gets lost.

### The pattern, five for five

Every "missing domain" the plan named turned out to be already-served, unbacked, or not a
domain: the artifact pair, the associate flow, fulfillment, outbox events, business config,
integrations, modules, security. The plan's lists were written from intent; the code
consistently said something else. Checking each against real backing before building saved
six endpoints that would have shadowed existing data — and the one genuine write-side
problem (four routers recording workflow transitions with one authorization check between
them) was on no list at all.

**Verification.** Contract **217 paths**, drift clean. ruff + format clean across 745 files.
mypy **42/14**. Full real-infra suite **2057 passed, 3 skipped** — the third skip is the
worker-script assertion, correctly skipped in the container, whose copy omits `scripts/`.

## Wave D3 — one configuration release lifecycle, and it is the graph

Status: DONE (`0612612`, `fec00b1`, `870e066`). Option A from the brief, chosen by the
owner. The decision this ledger carried as blocking for four waves is resolved.

### What was decided, and against what

Two lifecycles existed: a checksum-hardened Mongo one (`ReleaseService` /
`ActivationService`, DRAFT→VALIDATED→APPROVED→ACTIVE) that **no production path
constructed**, and the graph one (DRAFT→VALIDATED→RELEASED→SUPERSEDED→ARCHIVED) that
`main.py` wires and the runtime reads. Measured, not assumed: Neo4j held **0** release
nodes, Mongo held **3** leaked test fixtures. Nothing to migrate. Full measurements in
[CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md](CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md).

The hardened lifecycle was the dead one. That is the whole shape of the problem.

### Slice 1 — one transition table (`0612612`)

The state machine was written out three times: both repository implementations and the
Data Console router's early 409 check. Now `RELEASE_TRANSITIONS` plus `transition_allowed()`.

Eleven parametrised cases pin exactly what the three copies did, so the extraction is
provably behaviour-preserving, and a structural test bans the literal shape they had.
`RELEASED` deliberately has no outgoing transition: a release leaves it only by being
superseded when its successor publishes, and exposing `RELEASED → SUPERSEDED` as a
promotion would let an operator retire the live configuration with no replacement while
production refuses to start without one.

### Slice 2 — the checksum verifies instead of stamping (`fec00b1`)

**Two corrections to the brief, both found by reading the code rather than trusting the
document.**

The brief said the graph path does "no checksum recompute". It does — and more than that,
`ConfigurationSnapshotBuilder` recomputes and *compares* on every load of the active
release. That is the **strongest** check in the system: it catches tampering after
publication, not only between validation and publication.

What was genuinely missing was verification at *promotion*. Both repositories recomputed
and **overwrote**, so a domain edited between VALIDATED and RELEASED was adopted rather
than caught, and the release published cleanly — then failed at the load-time check, which
in production means the process will not start. Detection existed; it fired at the worst
possible moment and attributed the failure to the wrong event.

The lifecycle now has a freeze point. `save_draft_domain` already refuses to touch anything
past DRAFT, so VALIDATED records the checksum and RELEASED recomputes and compares, raising
`ConfigurationIntegrityError`.

**There were three hash implementations, not two** — the two repositories and the snapshot
builder, coupled by nothing but all three having been written the same way. That was proven
the hard way: changing one broke five tests with "production cannot start". All three now
share `compute_release_checksum`.

**A test found a defect in code that was only supposed to be moving.** All three
concatenated key and payload with no separator, so `("ab", "c")` and `("a", "bc")` hashed
identically. Ambiguous encoding is a defect in an integrity primitive whether or not the
current domain keys can express it, so the shared helper is length-delimited. This changes
every checksum value — safe, because the value is only ever compared against one computed
by the same function, Neo4j holds no releases, and the failure direction is closed.

### Slice 3 — the Mongo lifecycle is deleted (`870e066`)

1132 lines: both services, both test files, and `RELEASE_SERVICE_TRANSITIONS`.

**What was discarded, stated plainly:** a compare-and-swap activation behind a unique
partial index, a single-transaction supersede-and-activate, and barrier-synchronised
concurrency tests asserting exactly one winner. That was the better-engineered lifecycle.
It also had no callers and no data while the live path's checksum was only a stamp. Git
keeps it, and `expected_head_revision` may already be an equivalent CAS — worth checking
before anyone rebuilds it.

`domain/release.py::ReleaseStatus` survives with a docstring saying why: the manifest
pipeline still parses a status field with it, but `APPROVED`/`ACTIVE` no longer name any
state the platform transitions through, and reaching for it to model a promotion would be
a mistake.

Prose was updated rather than left to rot — `configuration/api/router.py`'s "no mutation
surface" comment described a blocker that no longer exists, and
`configuration/README.md`'s lifecycle and activation sections documented the deleted
services in detail.

**Verification.** ruff clean. mypy **42/14**, improved from the 47/16 baseline that had
held since Phase 4 — five errors lived in the deleted modules. Suite **2033 passed, 2
skipped** (down 16, all from the deleted test files).

## Wave D4, slice 4 — return creation is single-sourced, and was already

Status: DONE (`7c700e3`). **No production code changed**, because nothing needed to.

### The third claim that did not survive checking

The "associate flow drives the same session by another route" item is, like the artifact
pair, already resolved:

* `OperationalRepository.create_return` is the **only** writer to the returns collection,
  and it has exactly two callers.
* `POST /api/v1/returns` refuses anything but `channel="SYSTEM"` with a 409 naming the
  associate flow; `submit_details` creates with `channel="ASSOCIATE"`. The entry points are
  partitioned by channel, not competing.
* `start_chat`/`continue_chat` do **not** reimplement `start`/`continue_discovery` — they
  resolve free text into anchors and delegate. A natural-language adapter over the
  structured API, one implementation underneath. So `/chat` versus `/messages` is not a
  duplicate pair either, though the route list makes it look like one.

### What was missing

Anything holding it true. The partition rested on a single untested `if` in a router.
Deleting that line turned both endpoints into general-purpose return creators with nothing
failing — verified by deleting it, watching two tests fail, and restoring it.

Eight tests. The HTTP ones need no datastore: 409 means refused by the channel guard before
anything was resolved, 503 means the caller cleared it. Asserting "not 409" would have
passed with the guard gone, so the SYSTEM cases assert 503 exactly, and one case covers the
omitted-`channel` default not being a way around it. The structural ones pin the caller set,
forbid direct writes to the returns collection anywhere else, and fail if a chat method
stops delegating.

**Noted, not built.** `channel` allows `CUSTOMER` and nothing creates one today. The slot is
recorded rather than filled, so whoever builds customer-initiated intake finds it reserved
instead of adding a third entry point.

**Verification.** Suite **2017 passed, 2 skipped**. ruff clean, mypy 47/16.

## Wave D4, slice 3 — the stage-action overlap, and what it was actually costing

Status: DONE (`f7244fd`).

### The overlap is a shape mismatch, not two implementations

`POST /returns/{id}/pickup-actions` submits an **action**: it writes a pickup request and
derives the workflow event from it. `POST /production-returns/{id}/events` submits the
**transition**, with a caller-supplied event id and a free-text evidence reference.

Design doc §9.1 settles which is right — progress is action-driven, and "each of these
submits *intent*; the orchestrator evaluates stage prerequisites and decides whether a
transition occurs." `/events` inverts that.

It survives anyway, and that is recorded rather than glossed: **eight event types have no
action endpoint** (`RECEIPT_CONFIRMED`, `LICENSE_PLATE_ASSIGNED`, the three `*_NOT_REQUIRED`
waivers, `PRODUCT_DISPOSITION_COMPLETED`, `VENDOR_RECOVERY_REQUIRED`,
`VENDOR_RECOVERY_COMPLETED`) and `/events` is their only path. Removing it would strand
eight human acts. Building those eight action endpoints is what would let it go.

### What the overlap was actually costing

Verified before fixing, not inferred. Because each path derives its own event id, one real
carrier booking recorded through both produces two ids for one fact — and the state machine
**accepted the second**.

Neither existing guard caught it:

* `applied_event_ids` is for a retry of the *same* event.
* `_validate_transition` checks preconditions, and a transition's preconditions stay
  satisfied after it occurs. `CARRIER_BOOKING_CONFIRMED` requires `bol_tendered`, which
  never goes back to false.

So the second application appended to `applied_event_ids` — carried in Temporal workflow
state, so it grows without bound — and re-ran `_project_business_event`, writing a second
`shipment_events` row. The repository upserts on `(sourceSystem, sourceEventId)`, and the
two paths derive that key differently: the action path passes
`LOGISTICS_CONFIRMATION` + `{pickupRequestId}:{action}:{version}`, while `/events` falls
back to `PLATFORM_EVIDENCE` + `{eventType}:{free-text evidence reference}`. Different keys,
no collapse. **The evidence record showed one booking twice.**

### The fix

`_already_recorded` refuses a new event id for a transition whose effect is already
recorded. The two idempotency rules now differ deliberately:

* **Same event id → silent no-op.** At-least-once signal delivery depends on it; if this
  ever starts raising, every duplicate delivery becomes a 409.
* **Different event id for a completed transition → refusal.** It is either a duplicate
  report or a second real occurrence, and both need a human rather than a second
  application. The message names the event and says to resend the original id.

Nineteen of twenty types key on the flag they own. `PHYSICAL_HANDOFF_CONFIRMED` shares
`physical_return_complete` with `RECEIPT_CONFIRMED` and `PHYSICAL_RETURN_NOT_REQUIRED` —
correct rather than approximate, since a handoff after receipt is out of order and a
handoff after the physical return was waived is contradictory. `CANCELLED` keeps the older
shape: once cancelled, every further event is silently ignored, because a late signal for a
cancelled return is expected rather than exceptional.

### A test that was abandoned, and why

The first draft walked the state machine per event type to reach each transition and try it
twice. It needed three corrections in a row — stop before the terminal short-circuit, avoid
the waiver shortcuts, declare vendor recovery before closure — each pushing more of
`_validate_transition`'s ordering into the test. It was dropped: a test that has to
reconstruct the rules it checks ends up asserting its own copy of them.

The exhaustive part is now structural — `_already_recorded` is total over the enum, and a
missing entry is a `KeyError`, i.e. a 500 — plus a check that nothing reads as already
recorded on a fresh return, which is what catches an inverted marker. Behaviour is covered
at three lifecycle positions: early, mid (six preconditions deep), and a waiver. **That is
less coverage than intended**, recorded here rather than left to look complete.

**Verification.** 10 tests. Suite **2009 passed, 2 skipped** — nothing in the tree depended
on the double application. ruff clean, mypy 47/16. Status codes per path are unchanged and
still differ (409 from `/events`, 422 from pickup-actions, a deferred-signal audit row from
support); pre-existing, not widened here.

## Wave D4, slice 2 — `/artifacts` and `/evidence`, and the name that caused the confusion

Status: DONE (`bc1baf7`). This closes the "duplicate artifact pair", by establishing that
there was never one.

### The correction

Two earlier entries in this ledger, and `canonical_returns.py`'s own docstring, recorded
`GET /{id}/artifacts` and `GET /{id}/production-artifacts` as "a genuine duplicate pair"
and placed the duplicates "on the write side". Neither part held:

* `/artifacts` returns the document-artifact list.
* `/production-artifacts` returns the return's **entire evidence record** — eleven
  collections plus the embedded session and timeline — of which document artifacts are one
  field.

They shared a word, not an implementation, and both are reads. The endpoint is named after
one field of its own payload, which is how the two stayed mistaken for competing
implementations long enough to be written down as a duplicate and carried forward through
three ledger entries without anyone opening both files.

### What shipped

`GET /api/returns/{id}/artifacts` and `GET /api/returns/{id}/evidence`. The second is typed
as `ReturnEvidence` rather than the legacy opaque `dict[str, Any]`, so the contract names
the collections. Entries stay `dict[str, Any]` on purpose: they are projections of
documents owned by the physical-operations and integration modules, and modelling them here
would put eleven schemas in a domain that does not own them.

**`/evidence` is deliberately narrower than the endpoint it supersedes.** The legacy one
also embedded the session and the first 1,000 timeline events. Both have canonical
endpoints of their own, so carrying them forward would have made this a third way to read a
session and a second way to read a timeline — more surface, described as consolidation.

Both superseded reads now carry `deprecated=True` with the replacing path in the summary,
so the generated contract and the frontend's types say what to move to. The legacy paths
keep working: `production-artifacts` has one consumer (`frontend/src/api/operations.ts`),
and `/artifacts` has none — its name was simply being shadowed.

**This sets up the first actual reduction.** Wave F deleting `return_artifacts.py` — a
single-route module, now fully superseded — takes the return-domain router count from nine
to eight.

**Verification.** 14 tests. The read tests use a stub repository and assert *which*
accessors each endpoint calls: every collection returns the same shape, so a handler wiring
two fields to one accessor would otherwise produce a well-formed response with silently
duplicated data. Also asserted: no `_id` escapes, and a sub-resource 404s for a missing
session having read nothing. Contract **211 paths**, drift clean on consecutive runs,
frontend `tsc -b` clean. Suite **1999 passed, 2 skipped**. mypy 47/16.

## Wave D4, slice 1 — one authorization table for production return events

Status: DONE (`5fdc17f`). The real write-side duplicate, found while looking for the one
above.

### Four routers record production workflow transitions; one checked authorization

`ProductionWorkflowCoordinator.record_event` has seven call sites.
`api/production_workflow.py` gated it with `_authorize_event`, a per-event-type role table.
`physical_operations`, `return_support` and `warehouse_placement` reach it as a *side
effect* of a different action and never consulted that table — they relied on their own
route-level role dependency happening to be a subset of it.

| Route | Role dependency | Events it can emit | Subset held? |
|---|---|---|---|
| `POST /returns/{id}/pickup-actions` | logistics | `CARRIER_BOOKING_CONFIRMED`, `PHYSICAL_HANDOFF_CONFIRMED` | yes |
| `POST /warehouse/returns/{id}/bay-assignment` | warehouse | `WAREHOUSE_PROCESSING_COMPLETED` | yes (exact) |
| `POST /associate-returns/conversations/{id}/details` | associate | `DISCOVERY_CONFIRMED`, `RETURN_DETAILS_CONFIRMED`, `SUPPORT_REQUEST_CREATED` | yes |
| `POST /return-support/work-items/{id}/actions` | support | + `BOL_TENDERED` | **no** |

The three that held, held by coincidence. Nothing connected `require_logistics_roles` in
one module to the `CARRIER_BOOKING_CONFIRMED` entry in another, so narrowing the table
would have silently left the implicit path open.

The fourth did not hold. `return_support.apply_action` emits `BOL_TENDERED` when a support
user records LTL/BOL shipping instructions, and the table listed only logistics roles for
it. A `return_support` user was refused that transition on
`POST /production-returns/{id}/events` and allowed it via the support action — same
transition, two answers.

### The decision, and why

Resolved in favour of the support path (owner's call, offered against the alternatives of
refusing it or recording the asymmetry as intentional). Evidence for this reading: the
emission labels itself `sourceSystem="OMC_OR_SUPPORT_READBACK"`, naming support as an
intended source, and `_validate_transition` already requires `shipping_instructions_issued`
first — so it is a follow-on to something support legitimately did, not an independent
logistics act. Refusing it would have broken a workflow that works today.

### What shipped

* The table moved to `operations/production_event_authorization.py`, keyed on the
  `security.roles` constants rather than string literals. A typo in a literal reads as "no
  role may do this" — fails closed, invisible until someone is refused for no reason.
* `record_event` takes a **required** `actor_roles` and enforces the table itself, so
  authorization is a property of recording the event rather than of remembering to check
  first. Required, not defaulted: the caller who forgets is the caller who most needs it.
* All four routers authorize *before* resolving dependencies or mutating. Two of them
  mutated first, so a late 403 would have left a partial write. `return_support` authorizes
  its whole emission set up front, because one action can produce two events.
* `production_events_for_support_action` puts the "does this tender a BOL?" condition next
  to the support domain, and the router's emission branch reads that same result — one
  condition, not two copies.
* The dependency simulator and its workflow bridge pass a named `PLATFORM_SERVICE_ROLES`,
  so `grep` enumerates every place the platform acts as itself rather than for a human.

**A latent bug fixed on the way.** An event type missing from the table raised `KeyError` —
a 500, not a 403. It fails closed in effect but reports a forgotten table entry as a server
fault, indistinguishable to the caller from a role problem. Now typed, with an
exhaustiveness test that makes the branch unreachable in a shipped build.

**Verification.** 26 tests. Both the unit sweep and the HTTP-level test were confirmed
against a reverted `BOL_TENDERED` entry — they fail naming the exact escape, so they catch
the original defect rather than merely describing it. The HTTP tests need no datastore: 403
means refused before anything was resolved, 503 means the caller cleared every gate. Suite
**1989 passed, 2 skipped**. mypy 47/16.

**One thing deliberately not tested.** After the consolidation, no HTTP request can be
refused *by the event check* on those three routes — every role their dependencies admit is
one the table permits, which is the invariant. Cross-lane callers are refused by the route
dependency, which runs first. Contriving an HTTP 403 out of the event check would mean
breaking the invariant to observe it, so those cases assert the outcome without asserting
which layer refuses.

## `GET /api/session` — Wave E is now unblocked on the backend

Status: DONE.

Wave E1's report named two backend dependencies. The four canonical domains were the first
and are published. **This is the second**, and it was the one actually holding the frontend
back: the platform has always resolved a `Principal` per request — middleware sets
`request.state.principal`, every `require_*_roles` dependency reads it — and never returned
it anywhere. So the frontend could not know who the user is, and its capability hook
**fails open**, reporting `granted` when no principal is available because failing closed
would have blanked the console for everyone.

`GET /api/session` returns the caller's own subject, roles and capabilities.

**Roles are translated to capabilities server-side.** E1 mirrored `READ_ROLES`/`WRITE_ROLES`
in TypeScript (`shared/rbac.ts`) because there was nothing else to do; that is a second copy
of an authorization rule, and the copy that drifts is always the one nobody re-reads.
Deriving from the same frozensets the dependencies enforce means one definition. A test
asserts every role in `READ_ROLES` grants something and every role in `WRITE_ROLES` grants
write, so adding a role to `roles.py` and forgetting this endpoint fails loudly.

**Two deliberate decisions.** The endpoint carries *no* role dependency: guarding it with
`require_read_roles` would answer 403 to precisely the caller who most needs an answer —
signed in, no usable role — and the UI would render a failed request instead of "you have
no access". A caller with no principal at all is different and gets 401, because there is
nothing truthful to return. And capabilities are coarse (`config:read`, `config:write`)
because that is the granularity the routes actually enforce; finer-grained ones would let
the UI promise precision the server does not deliver.

**Backend authorization remains authoritative.** This is presentation input. A caller who
forges a capability list client-side gains nothing.

**A test defect the container caught.** The first draft selected roles with
`next(iter(frozenset))`, whose order varies with `PYTHONHASHSEED`; it passed on the host and
failed in the container because the arbitrary pick was sometimes `CONSOLE_ADMIN`, which is
also a write role. Selection is now sorted, and the file was re-run under four hash seeds.

**Verification.** 6 tests. Contract regenerated: **209 paths** — `/api/graph-schema` 13,
`/api/ai` 5, `/api/config` 3, `/api/returns` 3, `/api/session` 1. Full suite **1962 passed,
2 skipped**; frontend `tsc -b` clean. mypy 47/16 across 496 files.

### What Wave D still owes, and to whom

The published contract is **read-only**, so E2, E3 and E5 shipped without the actions
their phases specify. Details in the Wave E section; the four unblocking items are listed
at the end of it.

Open, and independent of E: see **"Wave D: what remains"** above, which is the single
maintained list. This paragraph used to hold a second copy; two lists of the same open items
drift, and the copy that drifts is always the one nobody re-reads.

## Closing what D2 unblocked in E5 — Respond Manually

Status: DONE (`1df6133`). The first Wave E screen with a working mutation.

D2's operator API landed two routes — unseal a held request, answer it — so the AI Control
Center's manual-response editor is real rather than named-as-unavailable. Claim, Generate
Candidate, Replay and Release still have no route and are still declared rather than
rendered as buttons that would 404.

### A live bug the wiring exposed

`InterceptionRow` was transcribed in **snake_case**; the endpoint emits **camelCase**. Every
field but `status` rendered as `-`.

Nothing failed, and the reason is worth keeping: the fields were all optional, a choice made
so an absent field could not break the screen. It hid a *total* mismatch instead — a fully
broken queue looked identical to an empty one. They are required now, so the next divergence
is a type error rather than a blank column. Two columns went with the fix: `claimed_by` and
`response_origin` do not exist on this endpoint; `expiresAt` and `answeredBy` do.

Verified by reverting the fixture to snake_case and watching five tests go red.

### What the screen enforces

The prompt is fetched only when a responder opens, never with the queue — it is sealed at
rest because it can carry rows read out of a customer's database. The test asserts this by
*counting calls*, not by inspecting markup: "did not render it" and "did not fetch it" are
different claims and only the second matters for a sealed payload.

Submit stays disabled until the prompt has loaded. Answering a prompt you have not seen is
the failure a manual path exists to prevent, and the backend cannot tell the difference. A
409 — somebody answered first — is surfaced verbatim rather than retried, because the second
operator needs to know their text was not recorded.

### A stale reason corrected

The Configuration tab said it was read-only "until the release-lifecycle decision in Wave D3
is settled". That decision *is* settled. It stays read-only for a different reason now — no
mutation surface is built on `/api/config` — and the tab says so. A stale explanation is
worse than none: it sends the next reader to solve a problem that is already solved.

**Verification.** Seven tests. `tsc` clean, eslint clean on the touched files (one
pre-existing error remains in `CopilotV2Page.tsx`, untouched). Frontend suite **88 passed
across 27 files**. Staged by explicit path, per the convention this frontend adopted after
an earlier `git add -A` swept in a concurrent session's work.

## Wave E / Phases 17–21 — the four-domain frontend

Status: **all five phases built and pushed** (`df2434f`, `31b2500`, `060fcd6`, `a90f6c6`,
`d258f0d`, plus the E1 backend in `f3112b6`). Every domain route in the shell resolves to a
real screen.

**Every screen was read-only, and that was the finding below rather than a scoping choice.**
Two of the four blockers have since closed, and E5's manual-response flow is wired against
them (`1df6133`) — see "Closing what D2 unblocked in E5". The rest still waits on canonical
mutations.

### Correction to "Nothing to Wave E. E2–E5 can be built against the published contract"

The contract is published and E2–E5 were built against it, so the sentence is half right.
What it misses is that **Wave D published read surfaces and no mutations anywhere**, so
every action the five phases specify has no route to call:

| Surface | Routes | Mutations |
|---|---|---|
| `/api/returns` | list, session, timeline | none |
| `/api/config` | runtime, releases, releases/{id} | none |
| `/api/ai` | routes, tasks, metrics, metrics/summary, interceptions | none |
| `/api/graph-schema` | 13 routes incl. mutations | analyses/drafts only |

`/api/graph-schema` is the exception and is why E4 has real validate/approve controls.

Each screen names its own gap in place rather than faking it, so the missing surface stays
visible to whoever opens the file:

- **E2** — no structured actions, no decision controls, no approvals, no conversation
  panel. Wiring them to the legacy routers would add a tenth way to mutate a return, which
  is exactly what D4 is holding the line against.
- **E3** — 2 of 9 tabs have a canonical endpoint. No promotion controls: see below.
- **E5** — Respond Manually and the response editor are now wired (`1df6133`). Claim,
  Generate Candidate, Replay, Release and Cancel still have no backend route.
- **E4** — no graph canvas *in the frontend*. Its backend blocker closed in `23abcdc`;
  see below.

### Phase 17's stated premise was wrong, and following it would have caused the harm it warns about

The plan says "the role model does not exist... **Build it in this phase**". It did exist:
nine roles and ten role-group dependencies in `data_console/api/auth.py`, enforcing across
**34 importing modules**. Building a second one would have produced two role vocabularies
with the *old* one still enforcing — the same shape as the configuration-lifecycle split
Phase 15 documents.

What genuinely did not exist: a **capability** layer, a canonical home (Data Console is
retired in Wave F), and the principal endpoint. So the model moved to `security/roles.py`,
`security/capabilities.py` and `security/authorization.py` with `data_console/api/auth.py`
left as a re-export shim — the same treatment D1 gave `ai_gateway/`, deletable in Wave F.
A test asserts the shim's role sets stay identical to the canonical ones, and another
asserts its `request: Request` annotations survive: FastAPI resolves `Depends(...)` by
inspecting them, so widening one breaks injection at runtime in all 34 importers while
still importing and type-checking cleanly. That was caught by writing the test, not by
review.

`/api/principal` reports subject, roles and capabilities, and deliberately does not report
the role-to-capability table — publishing it would make the role model part of the frontend
contract, which is what the capability layer exists to prevent. 401 for an unauthenticated
caller rather than an empty capability set, because the shell must tell "not signed in"
from "signed in with nothing granted".

`App.tsx` had to change and the plan was right to flag it: its fallback redirected
everything outside `/v1` and `/v2/...` into the legacy app, so the four domain routes were
unreachable. A test pins that the fallback still *would* swallow them, so the new branch
cannot quietly become dead code.

### The analyzer serializes counts, never the draft shape — E4 has no canvas

Status: **backend closed** (`23abcdc`). The frontend canvas is not built.

`/api/graph-schema` returned `entity_count` and `relationship_count`. `draft.shape.entities`
existed in the domain model and `api/drafts.py` read the counts off it and discarded the
rest. **No route returned the entities and relationships a canvas would draw.** The column
stated that instead of rendering invented structure from two integers.

`GET /drafts/{id}/shape` now returns entities with their properties (type, source field,
transformation), identifier properties, ownership and sync mode; relationships with both
endpoints and cardinality; indexes and constraints. That also gives the Properties,
Mapping, Indexes and Sync tabs their backing data — all four were named by the required
layout and had none.

**The ledger's "one backend change" was optimistic, and the correction is the interesting
part.** Two decisions the obvious version would have got wrong:

*The shape is typed at the API boundary, not in the domain.* `GraphSchemaShape` is a plain
mapping on purpose — it is the *result* of applying typed mutation commands, never an
editing surface, and typing it in the domain would add a second place every command has to
satisfy. The cost of leaving it untyped is that a command could start writing a
differently-named key, which would serialise as a missing field and quietly stop the canvas
drawing an attribute. The view models set `extra="forbid"`, so it raises instead. No current
command can produce such a key — which is exactly why the guard is tested rather than
assumed.

*It is a separate endpoint from the counts, not an addition to them.* Counts are O(1) and
are what a draft *listing* needs; a real source's schema is unbounded, and inlining it would
make every listing pay for a payload only the canvas reads. A test fails if the shape ever
appears on `GET /drafts/{id}`.

An empty draft returns an empty shape rather than 404 — otherwise "no entities yet" is
indistinguishable from "no such draft".

Seven tests. Gate: ruff clean, mypy unchanged at 42/14, contract PASS at 218 paths, full
real-infra suite 2064 passed / 3 skipped.

The 15 lint errors that briefly held this commit were F811: importing the sibling module's
`client`/`persistence` fixtures by name collides with the parameter names every test binds
them to. Both other modules in the package declare their own fixtures, so this one does too
— a `conftest.py` would have changed fixture resolution for eight files to save six lines.

### E3 has no promotion controls because the D3 decision is still open

Two release lifecycles exist. `ReleaseService` — which recomputes checksums on
VALIDATED→APPROVED and APPROVED→ACTIVE — is constructed nowhere outside
`tests/configuration/test_release_lifecycle.py`. Production runs Data Console's hand-rolled
transition table with no recompute. An Approve or Activate button in the canonical UI would
silently bless whichever one it happened to call, **on every future promotion**. The
`/api/config` router's own comment records the same gap.

Redaction stays server-side: `redact_secret_values` scrubs resolved secrets before the
response is built and leaves `vault://` references legible so an operator can see which
secret a binding points at. The UI adds no masking of its own — re-masking would hide those
references and imply the browser is a security boundary it is not.

### Queue visibility in E2 is presentation, and the code says so

`/api/returns` authorizes on read roles alone, so every reader receives every session.
Hiding a queue would restrict nothing. Queues are shown to anyone who can read, and
per-action RBAC becomes meaningful when there are actions to gate. Claiming otherwise would
have been security theatre.

Queue membership is derived from session state rather than stored, so a return moves queue
the moment its state does; a test pins that a closed return leaves every active queue
rather than lingering in Support forever.

### Working-tree hazard, recorded because it cost real work

Wave E was built in the shared checkout while another session committed into it. Commit
`f3112b6` — message "test(lifecycle): drive the generation lifecycle end to end against real
infra" — **swept the entire E1 backend into itself**: `canonical_principal.py`,
`security/{roles,capabilities,authorization}.py`, `data_console/api/auth.py` and both test
files, none of which it names. It is pushed, so amending it now means force-pushing a branch
another session commits on. This entry is the record of what actually landed there.

Every later Wave E commit staged files **by explicit path**, never `git add -A`, for that
reason. Two earlier Wave D worktrees were abandoned uncommitted and came within one
`git clean` of being lost (recorded under D1); the same class of hazard, the same tree.

### Gate receipts

- Frontend: `tsc -b` clean; `eslint --max-warnings=0` clean except one pre-existing error in
  `features/copilot-v2/CopilotV2Page.tsx:469` (confirmed pre-existing by stashing the change
  and re-running); `vitest run` **81 passed across 26 files** (from 70/25 at session start);
  `npm run build` succeeds and `check-bundle` finds no mock artifacts.
- Backend (E1 only): `pytest tests/security tests/api` 32 passed; `mypy` clean on all 8
  changed files; **194 passed** across the auth-touching suite (`-k "auth or role or api or
  router or support or warehouse or associate or console"`), which is what proves the shim
  still carries its 34 importers.
- OpenAPI: regenerated for `/api/principal` and synced across all four snapshots
  (`openapi/`, `backend/openapi/`, `frontend/openapi/`, root `openapi.json`);
  `check_openapi_drift.py` **PASS**.

### What unblocks the rest of Wave E, in dependency order

1. ~~**The D3 lifecycle decision**~~ — **settled** (`870e066`). The graph lifecycle is
   authoritative. This unblocked nothing on its own: E3 still needs promotion *endpoints*,
   which do not exist.
2. ~~**D2's operator API**~~ — **partly built** (`04e5ec3`). Unseal and answer exist and E5
   uses them. Claim, Generate Candidate, Replay and Release still have no route.
3. ~~**D4's write consolidation**~~ — **done** (`5474e54`). `POST /api/returns` and
   `POST /api/returns/{id}/events` replace five legacy routes, and the read-only test is
   replaced by an enumeration of the write surface rather than deleted. Two defects fell
   out of it: the two legacy cancellation paths disagreed about the discovery lock, and a
   rejected transition hung for the full RPC deadline because the workflow declared no
   `failure_exception_types`. See "Wave D4 — the canonical write surface".
4. ~~**Serialize the analyzer draft shape**~~ — **done** (`23abcdc`). `GET
   /drafts/{id}/shape` exists and E4's canvas has data to draw. It was not "one backend
   change" — see the section above for the two design decisions that made it three. The
   frontend canvas itself is still unbuilt; nothing backend blocks it.

**All four are closed.** Wave E's backend dependencies are met; what remains in E is
frontend work, which the owner has taken.

## Wave H1 - the static gate, closed by reading the protocols

Status: DONE (`19c356b`, `a84718f`). mypy strict: **20 -> 0** across 470 source files.

**Nothing was silenced.** No `type: ignore` was added; one was *removed*. Every error was
a protocol or annotation that did not describe its own implementations, and in each case
the declaration was wrong rather than the code.

**The recurring root cause: `object` and `Any` in a protocol are not "permissive".**
`sync/adapters.py` declared `GraphWriter.write(batch: object) -> object`. A protocol
parameter is contravariant, so `object` *demands* an implementation that accepts anything
-- which `Neo4jDynamicGraphWriter.write(batch: GraphMutationBatch)` is not. The declaration
intended to avoid an import and instead made itself unsatisfiable, and the `object` return
then forced a `# type: ignore[attr-defined]` on the receipt field read one line below.
Both real types live in packages the module already imported from, so naming them added no
coupling at all.

The same shape, twice more:
- `GraphDriver.session(**config: Any)` was *considered* as a fix and rejected for the same
  reason -- it demands acceptance of arbitrary keywords. The precise
  `session(*, database: str | None = None)` is what the driver actually offers.
- A bare `x: str` in a Protocol means **settable**, which a frozen Pydantic model cannot
  satisfy. `_StructureLike` needed `@property`. This is now the second place in the
  codebase where that bit; `RuntimeConfigurationView` had already documented it.

**Three latent defects surfaced that were not type errors:**
1. `sync/adapters.py` had a one-method local `SourceConnector` protocol declaring only
   `capture_high_watermark`. It made `SourceConnectorRegistry` *look* like the
   `SourceScanRegistry` it exists to be, while `resolve` returned something too narrow for
   `GenericSyncCoordinator` to accept. The duplicate is deleted; the canonical
   `SourceScanConnector` is used.
2. `_CountingConnector(inner: Any)` was missing `capabilities()` entirely -- an
   `AttributeError` for any caller. Nothing calls it today. Typing the parameter is what
   revealed it.
3. `order_agent.py` dispatched on `runtime.__class__.__name__ == "DynamicOrderAgentRuntime"`,
   which both returned `Any` and would accept any unrelated class sharing the name.

`_NoModulesYetConfigurationHandle` is a placeholder whose every method raises, and its
`current()` took no epoch while its `pinned` was sync -- against a protocol that passes
`RuntimeEpoch` and awaits. A placeholder whose signature does not match cannot prove the
thing it is standing in for.

---

## Wave F5 - data_console deleted, in two parts

Status: DONE (`8a0d81a`, `007326f`).

**Part 1: fourteen modules at zero consumers**, ~4,000 lines, plus three test files. Chosen
by a reachability closure seeded from every import crossing into the package from outside:
six modules reached, fourteen not. Contract unchanged at 107, because F1 had already
unmounted all of them -- which is the expected result and the reason the unmount came
first.

`test_inventory_api.py` was deleted one commit after F1 rewired it onto a standalone app.
That rewire was still right: F1 was an unmount, and presupposing F5's outcome inside it
would have made the unmount unreviewable as an unmount.

**Part 2: the six that were reached were never Data Console code.** They were shared code
living in its package.

`api/auth.py` was already a **Phase 17 shim** whose own docstring said "the import sweep
that deletes it is Wave F". It defined nine `require_*_roles` one-liners wrapping
`require_roles` -- which lives in `security/authorization.py`, so the wrapper and the thing
it wraps sat in different packages. 21 files swept. `audit`, `sources` and `configuration`
(renamed `releases.py`) went to `configuration/api/`; `runtime_validation` beside its
bootstrap; `probes` beside its API consumer.

**An architecture test caught a bad destination.** `probes.py` went to `platform/` first
and `test_platform_imports_no_domain_module` failed -- `platform/` is the dependency-free
kernel and probes imports `configuration.settings`. The test named the exact import.

Two references the sed sweep missed, both found by re-grepping rather than trusting the
rewrite: a different import form (`from ...api import auth as legacy_auth`) and a stale
entry in the analyzer's forbidden-import list. **Third occurrence of that shape** after F2's
two. Re-grep after every sweep.

---

## Wave G - infrastructure, scripts, config, docs

Status: G1, G2, G3 DONE (`4ac6b6e`); G4 done here.

**G1 - three Compose profiles.** Default now matches the plan exactly: ten infrastructure
and bootstrap services. `temporal-ui` and `seed-runner` moved to a new `dev-tools` profile;
`runtime-configuration-init` moved *into* the default, because it seeds the graph
configuration every process reads at startup and leaving it behind the app profile meant a
default stack that could serve configuration to nothing.

`containerized-app` keeps **eight** services, not the plan's five. `order-discovery-worker`
(Wave C3, built after the plan was written), `data-job-worker` and
`integration-outbox-worker` all exist with real entrypoints. Deleting working services to
match a list that predates them is the mistake this programme keeps catching; the
divergence is recorded in a test.

A `diagnostics` dev-tool now exists and replaces something worse:
`run_real_infra_suite.sh` expects a `c2-test-runner` container that nothing in the
repository creates -- a hand-made `python:3.13-slim` that source was `docker cp`-ed into,
which is why syncing merges rather than replaces and why a stale file in it already
produced one false green. The suite script is deliberately not rewired onto it yet.

**G2 - nothing to remove, which is the finding.** The phase asks for legacy compatibility
configuration to be deleted at zero consumers. Scanned, there is none. Every entry under
`backend/config/` is either read at runtime or named by the target design as
intended-but-unwired: `policies/` (four files) and `live_validation/data_assets.sampling.yaml`
have no loader but are in the design's configuration tree. Unimplemented design is not a
dead compatibility shim, so G2 deleted nothing and `backend/config/README.md` now lists the
declared-but-unloaded set, because the two states are indistinguishable from the
filesystem.

**G3 - mostly repairing what F4 broke.** `verify_mandatory_routes.py` and
`mandatory_routes.json` deleted (seventeen routes in a file F4 removed;
`validate_stage4_source.py` owns that assertion now).
`validate_stage4m_dependency_simulation.py` lost its six UI assertions and kept its backend
ones. `validate_stage4_source.py` itself needed two further updates for F5 and G1. **Three
separate gate scripts were left broken by F4**; all three pass now.

**G4 - the README was substantially false.** Corrections: `/v1` was described as the
canonical console; the repository layout listed `data_console/` and
`features/data-console/`; the containerized URL table pointed at `/v2/copilot` and
`/v1/associate/returns`.

And one that predates Wave F entirely: **the README documented eight `/api/v2/copilot/*`
endpoints that have never existed** and appear in no contract. Replaced with what actually
serves Order Discovery -- `/api/v1/associate-returns/*` plus the Temporal-hosted
`/api/v2/order-agent/.../turns` -- and the verification snippet that grepped the contract
for `/api/v2/copilot` now checks the four canonical prefixes.

---

## Wave E frontend - the four screens, wired

Status: DONE (`a2ba0dc`, `a6a0d6f`, `0c733c2`, `ff20569`, `1acbede`).

Each screen got what its phase specifies, and three of the four turned up the same class
of bug: **a frontend type naming values the backend never emits, failing silently because
the field was optional and the values were strings.** Third, fourth and fifth instances
after E5's snake_case interception row.

* **E2 - Return Business Copilot.** The action panel is one endpoint, `POST /{id}/events`.
  There is no Cancel button because cancellation is an event; a `cancel()` helper would
  re-introduce the split D4 just closed. All twenty event types are offered and the screen
  deliberately does *not* filter by stage - that would be a second copy of
  `_validate_transition`. A test asserts the count of twenty so the tempting filter fails.
* **E3 - Configuration.** `ReleaseStatus` named the Mongo lifecycle D3 deleted, so the
  Overview tab searched for `status === "ACTIVE"` and reported "No ACTIVE release found"
  in every deployment, including ones with a published release. Promotion buttons now come
  from `ALLOWED_PROMOTIONS`, mirroring `RELEASE_TRANSITIONS`.
* **E4 - Graph Schema Analyzer.** The canvas draws. A grid and an edge list rather than a
  layout engine: cardinality is the half a count hides, and that is what a reviewer needs.
  Writing the "one fetch" test found that the canvas and tabs shared a cache entry but
  refetched on each new observer.
* **E5 - AI Control Center.** Cancel wired. The stat tiles counted `CLAIMED` and
  `RESPONDED`, which `InterceptionStatus` does not have.

Also fixed: `npm run lint` was failing at HEAD on a pre-existing error in a legacy file, so
`04_run_frontend_quality.sh` could not have passed. And the four canonical screens were the
only ones unviewable without Docker, because `/api/principal` had no mock handler.

---

## Wave F4 - the legacy frontend, deleted

Status: DONE (`6a1a0aa`). 191 files, 18,572 lines.

**76 legacy routes, not the ~28 an earlier reading of this ledger claimed.** That figure
came from a truncated grep and was wrong in both directions that matter: it understated the
deletion and it understated what had no replacement.

Of the 76: 41 Data Console, 11 system tooling, 8 AI gateway, 14 return-domain, plus
`/overview`, `/seed-data`, `/v2/copilot` and `/v2/config`. The four canonical domains
replace the return-domain screens. **They replace nothing else.** The data browser, graph
explorer, inventory, workspaces, scenarios, jobs, imports/exports, AI studio and graph sync
have no canonical equivalent and are absent, not superseded. The owner chose that
explicitly when the alternative - deleting only the replaced routes - was offered.

**The survivor set was computed, not hand-listed**: a transitive closure from `main.tsx`,
the test setup and the mock worker gave 44 reachable modules and 168 unreachable. Across
229 files, hand-picking is how a still-imported module gets deleted.

**Deleting the legacy frontend briefly took down the canonical one.**
`RuntimeConfigProvider` blocks first paint on `/api/v1/runtime-config`, which in mock mode
was served by the Data Console handler file being deleted. Every canonical screen rendered
"Configuration Error". Caught by the replacement e2e - not by the 65 unit tests, and not by
the manual browser pass, which had run *before* the deletion. The four obsolete Playwright
specs drove only deleted routes; `canonical-domains.spec.ts` replaced them.

That left a real finding: **the four-domain shell has a hard dependency on a versioned
legacy route.** `/api/v1/runtime-config` survives F1 and F2 for that reason.

**It also broke a gate, and I shipped it.** `validate_stage4_source.py` asserts frontend
routes from `frontend/src/routes.ts`; F4 deleted that file, so the script raised
FileNotFoundError inside `05_run_contract_and_config_checks.sh` and two stage gates. Fixed
in `f7a44db`, which also repaired two *older* stale paths in the same script - the SQL
migration directory moved in `b19570f` and the provider package in `6ff5162`, so it had
been failing long before F4 for reasons nobody had noticed.

---

## Wave F1/F2 - unregister, then retire

Status: DONE (`3c633b8`, `944cb82`). Contract 223 -> 148 -> 107.

**F1 unregistered eighteen Data Console routers.** The scan the plan mandates: the
surviving frontend calls only the four canonical prefixes, `/api/principal` and
`/api/v1/runtime-config`; six shell scripts call `/api/v1/*` but none Data Console; nothing
self-calls over HTTP. One consumer existed - `validate_graph_evidence_api.sh`, which
nothing invokes - and it was retired with the routers rather than kept alive to justify
them.

**Unregistered, not deleted**, because `configuration.py`, `sources.py`, `audit.py` and
`auth.py` still export handlers the canonical `/api/config` router imports directly. That
is F5's work. One commit that both unmounts and rewrites is one where half the diff is not
reviewable.

**F2 retired the V2 shell**: two routers, the `return_platform/v2/` package, and four
pieces of lifespan wiring including a middleware hook that called `persist_all()` after
every `/api/v2/` mutation. `/api/v2/order-agent` stays - it is Wave C's Order Discovery
surface and merely shared the prefix, which is exactly why that prefix-scoped hook would
have fired for it.

`V2PlatformServices.bootstrap()` pointed at `config/v2`, a directory that does not exist.

**A scan of mine was wrong and the suite caught it.** I listed v2's test consumers with
`grep | head`, which truncated at ten lines and hid two files; collection failed on the
first run after the deletion. Without `head` the list was three, the third being a stale
`"return_platform.v2"` entry in the analyzer's forbidden-import list that would have gone
on asserting nothing imports a module that no longer exists. **`head` on a
prove-zero-consumers scan is how a deletion ships broken.**

---

## Wave D4 — the canonical write surface

Status: DONE (`5474e54`). Two routes replace five.

`POST /api/returns` (create) and `POST /api/returns/{session_id}/events`. The other three
legacy routes should not exist, and working out why found two real defects.

### The two ways to cancel disagreed, and consolidating them would have kept the wrong one

`POST /api/v1/returns/{id}/cancel` wrote `status: CANCELLED` straight to Mongo and released
the discovery lock — and never told the workflow. The workflow's own CANCELLED event
updated durable state *and* the session document, and left the discovery lock held.
Whichever a caller used, one of the two records was wrong.

The obvious consolidation — "cancellation is just an event now" — would have made the
leaking one the only one. `ProductionWorkflowCoordinator.record_event` now releases the
lock when `state.cancelled`, so the single canonical path does everything both did. Put in
the coordinator rather than the route because it has to hold for every caller, including
the workflow's own; a test asserts it there for the same reason.

### A rejected transition hung for ten seconds, on the commonest case

`apply_production_return_event` raises `ValueError` to *refuse* an event — out of order, or
an effect already recorded. `@workflow.defn` declared no `failure_exception_types`, and
Temporal's default is to treat a non-Failure exception as a workflow **task** failure: the
task retries forever, `execute_update` never returns, and the caller waits out the
10-second `rpc_timeout` before getting a generic 409.

For a UI that is not the rare case — it is the double-click and the screen acting on stale
state. Declaring `ValueError` makes those fail the *update* instead, immediately, with the
reason attached. The canonical handler maps `WorkflowUpdateFailedError` to a 409 carrying
the wrapped `ApplicationError`'s message (the same discrimination `orchestrator._failure_code`
already did) and `RPCError` to **503** — the legacy handler reported an unreachable Temporal
as 409, which invites a client to fix a request that was already correct.

### What replaced the read-only test

Not nothing, and not "writes are fine now": an enumeration. Consolidation that ends with
nine canonical writes instead of nine legacy ones has achieved nothing, so the write
surface is pinned to exactly two paths, and a test asserts there is no canonical `/cancel`
at all.

`create_return` now has three callers rather than two. That is recorded, with the note that
it drops back to two when Wave F deletes `api/returns.py` — and the channel partition is
enforced on both, so deleting either guard still fails something.

---

## Wave F3 — main.py is already a composition root

Status: criterion met, held by tests (`d14e5c1`). The size reduction belongs to F1/F2/F5.

F3 asks for "no business logic / provider selection / source-specific logic". Measured:
the only routes `main.py` defines are the two health probes, it runs no datastore query,
and it compares against no source technology. `_resolve_principal_provider` picks an
implementation from settings, which is what a composition root is for.

What makes the file 1089 lines is 42 router imports and mounts plus the bootstrap
sequence — and router mounting is on F3's own list of what belongs there. Restructuring the
401-line lifespan now would be churn that F1, F2 and F5 undo when they delete the routers
it is sequencing. So the work was to hold the criterion, not to refactor: four tests, one
of which asserts the mount count so a deletion that leaves a dangling mount fails, and the
count is the honest measure of how much cutover is left.

### Why F1, F2 and F5 are not being done now

The plan's own rule: "Before each deletion, prove zero legitimate runtime consumers with
one dependency scan." The legacy frontend at `/v1` still mounts ~28 routes against exactly
the routers those phases delete. The scan cannot come back clean until F4 removes it, and
F4 is frontend work the owner has taken. **These are gated, not skipped.**

---

## Wave D2 / Phase 14, slice 3 — the resume bridge (at-least-once)

Status: DONE. D2's "at-least-once resume worker" requirement is met.

**No second worker was built, deliberately.** One already existed and is good:
`platform/reasoning/resume_worker.py` claims `reasoning_resume_commands` under a lease,
delivers Temporal signals with exponential backoff, and deduplicates workflow-side on
`command_id`. A second worker beside it would have meant two lease disciplines, two backoff
policies, and two places to get at-least-once wrong. `InterceptionResumeDispatcher`
therefore **bridges** rather than delivers: an answered interception becomes a resume
command row, and the existing worker takes it from there.

**The interception record is its own outbox.** The answer and the resume intent live in
different collections, so one write cannot cover both, and a cross-collection transaction
would be another mechanism to keep correct. Nothing extra is written at answer time:
`ANSWERED` with no `resume_enqueued_at` *is* the queue. The only durable state is the
interception itself.

**At-least-once rests on a unique index, not on the stamp.** A crash between "wrote the
command" and "stamped the interception" replays the enqueue on the next pass. That is safe
because the command id is *derived* (`interception:{id}`) and `command_id` carries a unique
index, so the replay collides in the database rather than delivering a second signal. The
test for this deletes the stamp to simulate the crash and asserts exactly one command
survives — which is how this actually fails in production, and a test that only ran the
happy path would not have caught a random command id.

**One correctness detail found while wiring it.** The stamp writes through the *guarded*
store rather than the raw collection: `ai_interceptions` is `encrypted: true`, and a
plaintext write must be refused even when the field being added is only a timestamp. That
required promoting the metadata allowlist from private to shared so both writers declare an
identical set — two writers with different allowlists would be a slow leak.

**Two test defects the real database caught, both worth recording.** The first draft of
the real-infra tests *created* a unique index on `reasoning_resume_commands.command_id` --
but `system_store.yaml` already declares `command_id_unique` on it, so the second unnamed
index was an `IndexOptionsConflict`. Asserting the index instead of creating it is also the
better test: one that provisions its own indexes can pass while production lacks them. The
second reached for `SystemStore.collection()` on `ai_interceptions` to simulate the crash,
and the Slice 3R.6 encryption guard refused it -- the hardening working against a test, as
designed. The simulation now goes through the guarded `replace_one`.

**Still open in D2:** the API-process route wiring (its pool is built before the SystemStore
exists) and an operator console to answer held requests; `/api/ai/interceptions` and the
store's `request_payload` are the two halves it would need.

## Canonical `/api/ai` — the AI Control Center read surface

Status: DONE. **Wave D's completion condition is now met**: "backend exposes the four
canonical API domains needed by the frontend and all are generated into OpenAPI."

`/api/ai` exposes `routes`, `tasks`, `metrics`, `metrics/summary` and `interceptions`,
reading through the same `AIGatewayService` and `OperationalRepository` as
`/api/v1/ai-gateway`, which keeps working until Wave F.

**Structured observability, never private reasoning.** Phase 21's rule is "expose
structured node/action observability, not private chain-of-thought", and nothing here
returns a prompt, a completion or a model's working. `routes` returns health counters and
circuit state — the only place "why did that task fail over?" is answerable. `tasks`
returns policy, including `allowedProviders` and `allowedInputKeys`, which are what stop a
caller reaching a provider or sending a field the task was never approved for.

**`interceptions` returns identity and status only.** The held prompt is sealed at rest and
is deliberately off this surface: decrypting every pending prompt to render a queue would
defeat sealing them, and an operator scanning the queue needs identity and age, not
content. Whoever opens one to answer it fetches the payload explicitly.

**A D2 gap closed on the way.** The endpoint needed a queue and
`SystemStoreInterceptionStore` had none, so `list_pending()` was added — oldest first,
because the queue is worked in arrival order and the oldest item is closest to expiring
unanswered. Two real-Mongo tests cover it, including that an answered interception leaves
the queue (otherwise an operator is shown work someone else already did). The store is now
bound on `app.state` in the analyzer's degrade-safe block, where the SystemStore first
exists in the API process.

### Contract state

**207 paths.** `/api/graph-schema` 13, `/api/ai` 5, `/api/config` 3, `/api/returns` 3.
Drift check passes on consecutive runs; all four JSON snapshots and the generated `.d.ts`
agree.

**Verification.** Backend **1918 passed, 2 skipped**; frontend **64 passed** with `tsc -b`
clean against the regenerated types. mypy 47/16 across 490 files.

**What "Wave D complete" does and does not mean.** The four domains exist and are
published, which is what unblocks the frontend's E2–E5. Three phase-level items remain
open and are described in their own sections above: D2's at-least-once resume worker, D3's
mutation surface (blocked on the two-store lifecycle question), and D4's write
consolidation across nine routers.

## OpenAPI contract regeneration, and the drift check made trustworthy

Status: DONE. `openapi-drift` **passes** for the first time in this branch's recorded
history; the ledger previously listed it as an accepted failing condition.

### Why it had been failing, which was not what the ledger said

The ledger recorded "the committed snapshot is stale". The real cause was structural:
**two generators existed and produced different documents from the same commit.**
`backend/scripts/export_openapi.py` called `create_app()` with ambient settings;
`scripts/check_openapi_drift.py` called it with explicit test settings. Feature flags and
optional integrations change which routers mount, so the two disagreed —
`frontend/openapi/...` sat at 631 KB and `openapi/...` at 512 KB, both "current". **A
contract whose content depends on the exporter's environment is not a contract.**

There is now one generator with pinned `contract_settings()`, and the drift check imports it
rather than reimplementing it.

### Three defects in the check itself

1. **It was a checker that mutated.** On drift it reported failure *and* wrote the new
   content, so the second run always passed. A gate that repairs what it detects can never
   fail twice for the same reason — and locally it silently rewrote files the developer had
   not chosen to change. Now `--write` regenerates and the default checks, writing nothing.
2. **It deleted `frontend/openapi/return-platform.openapi.json` unconditionally and never
   regenerated it** — a file `frontend/package.json`'s `contracts:generate` and
   `contracts:check` both consume. The delete is gone.
3. **It covered two of five committed artifacts.** `backend/openapi/...`,
   `frontend/openapi/...` and the root `openapi.json` were unmanaged and had already
   diverged into three distinct contents. All four JSON snapshots plus the generated `.d.ts`
   are now covered and byte-identical (`0f1ac5cb9855…`).

The four-way duplication is itself the defect; removing it is Wave G. Until then they must
agree, because each is somebody's source of truth.

### Contract state

202 paths. Canonical domains now published: `/api/graph-schema` (13), `/api/config` (3),
`/api/returns` (3). **`/api/ai` is still absent** — Wave D's completion condition names four
canonical domains and only three exist.

**Verification.** Backend full suite **1916 passed, 2 skipped**; frontend **64 passed**,
`tsc -b` clean against the regenerated types; drift check exits 0 on a second consecutive
run, which is the property the old script could not have.

## Wave D4 / Phase 16, slice 1 — canonical `/api/returns` read surface

Status: read surface DONE, duplicate inventory DONE and executable. The write surface is
held back on purpose — see below.

`/api/returns` now exists with `GET ""`, `GET /{session_id}` and
`GET /{session_id}/timeline`, reading through the same `OperationalRepository` the legacy
router uses. Named `timeline` rather than the legacy `events`: the aggregate and the plan's
own domain list both call it a timeline, and the canonical name should follow the domain
rather than inherit an implementation word.

### The return domain is served by nine routers across six prefixes

**Three of them share `/api/v1/returns`** — `returns.py`, `physical_operations.py` and
`return_artifacts.py` — so the module owning a legacy path is not derivable from the path.
~~There is also a genuine duplicate pair: `GET /{session_id}/artifacts` in
`physical_operations.py` and `GET /{session_id}/production-artifacts` in
`return_artifacts.py`.~~ **Wrong — corrected in D4 slice 2 (`bc1baf7`), see that section.**
Those two share a word, not an implementation: one is the document-artifact list, the other
is the whole evidence record of which document artifacts are one of eleven collections.
Both are reads. This claim was written without opening both files and was then carried
forward through two more entries.

Phase 16's instruction is "resolve duplicate current implementations **before** deleting
anything". ~~and every duplicate is on the *write* side~~ — also wrong, for the same
reason; the genuine write-side duplicate was the production-event authorization split,
found later and closed in D4 slice 1. Publishing a canonical write surface first would have
added a tenth way to mutate a return rather than replacing nine, so the canonical surface is
read-only until the remaining duplicates are reconciled. A test enforces that, and another
enforces "no generic advance" on the canonical surface (the existing test covered only the
legacy one).

**The inventory is a test, not a table in a doc.** `test_the_number_of_return_routers_has_not_grown`
fails if a tenth return-domain router appears, so consolidation cannot quietly run
backwards while it is in progress. That decision paid for itself immediately: the inventory
was hand-written as *eight* routers and the test caught `return_agents.py`, which is exactly
what a prose list misses. The corrected count is nine, recorded in both the test and the
router docstring.

**Verification.** 4 architecture tests. Full suite **1916 passed, 2 skipped, 0 failed**.
mypy 47/16 across 489 files.

**Still open in D4** *(as of this slice; superseded by "Wave D: what remains" above, which
is the maintained list)*. The write consolidation itself: reconciling the two artifact
endpoints, the overlapping stage actions between `production_workflow.py` and
`physical_operations.py`, and the associate flow that drives the same session by another
route. Then the aggregate's remaining domains — support, fulfillment, warehouse, artifacts,
outbox events — need canonical read paths; only session, list and timeline are exposed so
far.

## Wave D3 / Phase 15, slice 1 — canonical `/api/config` read surface

Status: read surface DONE. **The mutation surface is blocked on a decision, not on effort**
— see the finding below.

`/api/config` now exists, versionless like `/api/graph-schema`, with `GET /runtime`,
`GET /releases` and `GET /releases/{id}`. Handlers read through the same
`ConfigurationGraphRepository` the Data Console router uses rather than reimplementing
anything.

**"Secrets are stored in Vault and APIs return references only" is enforced on the way
out.** `redact_secret_values` masks a resolved value under any secret-shaped key while
letting a `vault://` reference through untouched — an operator must be able to see *which*
secret a binding points at. It runs on the whole response, not on hand-picked fields,
because hand-picking is what eventually misses one; a test asserts every handler builds its
response through the single `_ok` helper, so a new endpoint cannot bypass the scrub. Key
matching is substring and case-insensitive (`apiKey`, `api_key`, `API_KEY`, `googleApiKey`)
and non-strings under secret-ish keys are left alone, since a `null` or a
`secretRequired: true` is structure rather than credential.

### The finding: there are two configuration release lifecycles, and the hardened one is wired to nothing

`ReleaseService` — the one that recomputes and compares checksums on VALIDATED→APPROVED and
APPROVED→ACTIVE, added in Slice 3R specifically to close that gap — is constructed in
**exactly one place in the repository: `tests/configuration/test_release_lifecycle.py`.**
No production path uses it.

What production actually runs is `data_console/api/configuration.py`'s
`promote_release_status`, which hand-rolls the lifecycle inline with its own
`allowed_transitions` table and **no checksum recompute**. So Phase 15's "activation and
adoption use the already hardened configuration/epoch mechanisms" is currently false, and
Slice 3R's hardening protects a code path nothing calls.

Adding canonical mutation endpoints on top of that would have made it three lifecycles, or
silently blessed whichever one the new file happened to call. The canonical surface
therefore ships **read-only** until it is decided which lifecycle is authoritative and that
one is wired. That is a real decision with real blast radius — it changes what happens on
every configuration promotion — and it is the blocking item for the rest of D3.

**Also still open in D3:** the plan lists sources, integrations, business config, modules,
security and audit alongside runtime and releases. Only runtime and releases are exposed
canonically; the others live under Data Console routers or do not exist yet, and mapping
each one is its own slice.

**Verification.** 10 tests. Full suite **1912 passed, 2 skipped, 0 failed**. mypy 47/16
across 488 files.

## Wave D2 / Phase 14, slice 1 — durable interception store

Status: store, provider, and route wiring done. The at-least-once resume worker and the
operator API remain open — see the end of this section.

`ManualFileProvider` writes JSON to `.manual_llm/requests/` **relative to the process CWD**
and polls a sibling directory for the reply. Every in-flight request is lost on restart, a
second replica cannot see the first's files, answering requires filesystem access to the
container, and nothing records who answered.

**Built.** `ai/interception/{records,store}.py` and
`ai/providers/durable_interception.py`. The interception, its sealed request payload and
its `ResumeCommand` are **one document**, which is how the plan's "persist interception and
embedded resume command atomically" is satisfied without a transaction anyone could forget.
Status transitions are compare-and-set filtered on `PENDING`, so two operators answering the
same request produce one winner and one `InterceptionNotPending` rather than a silent
overwrite.

**`ai_interceptions` is now `encrypted: true`.** It previously was not. A held request is
the full prompt a provider would have received — for the analyzer that includes block 5
UNTRUSTED SOURCE SAMPLE, rows read out of a customer's database — and, once answered, a
human's reply to it. Sealing the prompt while leaving the reply in the clear would be
theatre, so both live inside the envelope; only the fields an operator console filters on
stay queryable.

**Two plan rules turned out to be already satisfied by the existing design, and are now
pinned by tests rather than left to luck.** Human output reports `MANUAL` /
`manual-human-v1` and never the provider whose place the human took — a trace that recorded
it as a model's would corrupt any evaluation set built from it. And the provider returns a
plain `ProviderResponse`, so a human answer travels the identical schema-parse and
`inspect_output` path a model's does; validating in the provider would have created a
second, weaker validation path for the least trustworthy input in the system.

"No hidden chain-of-thought" is enforced by the record's shape: `Interception` is a frozen
slots dataclass with nowhere to put reasoning, and a test asserts a `reasoning` attribute
cannot even be assigned.

**Verification.** 12 unit tests plus 7 against real Mongo — including sealing, the
store-level refusal of a plaintext write, and two concurrent `answer()` calls producing
exactly one winner and one refusal. Full suite **1899 passed, 2 skipped, 0 failed**.
mypy 47/16 across 485 files.

### Slice 2 — wired into route construction

`build_routes` takes an optional `interception_store`. When present, MANUAL resolves to the
durable provider; when absent it still resolves to `ManualFileProvider`, so a bare `pytest`
run or a script with no platform Mongo keeps a working keyless manual path — which is most
of what MANUAL is for.

Wired in `scripts/run_order_discovery_worker.py`, which is the process that actually runs
MANUAL reasoning turns and already bootstraps a SystemStore *before* it builds routes. The
FastAPI process builds its pool at `main.py:577`, before the analyzer's
`bootstrap_system_store` at ~626, so wiring it there means reordering a deliberately
degrade-safe startup sequence. Left alone rather than reordered late in a long session;
the analyzer's MANUAL path therefore still uses the filesystem provider in the API process.

Three tests cover the wiring itself: a supplied store yields durable providers, no store
falls back, and **both report the same `(name, model)` to traces** — swapping the storage
backend must not change what a trace records, or historical traces stop comparing to new
ones.

**Still open in D2.** (1) **The API process** still builds its route pool before the
SystemStore exists (above). (2) **The at-least-once resume worker**: this provider still *blocks* a coroutine while a
human thinks, which is the polling model the plan wants gone. The record is already shaped
for the fix — `ResumeCommand` exists so a worker can complete the work without the original
coroutine — but the caller must be able to release its turn, which the Order Agent can
express (`interrupt()` + Temporal) and the analyzer cannot yet. (3) An operator API/console
to list and answer pending interceptions; `render_request_for_operator` is the only piece
of that in place.

## Wave D1 / Phase 13 — AI Gateway consolidation

Status: DONE for the migration and the single invocation path. D1 is **not** fully closed —
see the end of this section.

**Wave D had been started twice and abandoned twice, both times uncommitted.** Two agent
worktrees held the work with zero commits between them; it was one `git clean` from being
lost. Both are now preserved on their own branches (`6ff5162`, `2116665`) before anything
was merged.

They were complementary and **neither worked alone**:

- `worktree-agent-a8418bbe4d014f473` had the whole `ai_gateway/` → canonical `ai/`
  migration (`ai/providers/`, `ai/routing/{tasks,routes,selection}`,
  `ai/safety/{injection_guard,scope_guard,inspection}`, `ai/gateway/`), a deprecated
  re-export shim, and `ai/README.md`. It was **missing
  `gateway/structured_invocation.py`, which its own README documented.**
- `claude/vigorous-haslett-6cb188` had exactly that file (as `ai_gateway/structured.py`),
  the analyzer AI adapter and its 6 tests, the `GRAPH_SCHEMA_PROPOSAL_V1` task config, and
  the `model_gateway.py` collapse (−279/+45) — all written against the import paths the
  other worktree was deleting.

Merged the migration, then ported the four missing pieces onto canonical paths.

**The shim layer is kept deliberately.** `ai_gateway/` is now 15–33-line pure re-exports,
documented as such: ~20 modules outside the AI lane still import it, including `main.py`
and `runtime_factory.py`. The import sweep that deletes it is Wave F.

**One invocation path, for real.** `model_gateway.py` no longer reimplements retry,
failover, tier escalation and safety — it delegates to `StructuredOutputInvoker`, the same
path the analyzer adapter uses. That is the "reusable common components" requirement rather
than a description of one.

**Corrections made while landing it.** The README named the class `StructuredRouteInvoker`;
it is `StructuredOutputInvoker` — the doc was wrong, so the doc was fixed rather than
working code renamed.

**An architecture test that I had to narrow, and why.** The first draft of
`tests/platform/test_ai_lane_boundary.py` banned provider strings anywhere outside `ai/` and
found 37 hits — every one a validator's allowed-value set, a capability map built from
settings, or an API view model reporting which provider *did* serve. Recording the provider
that answered is the opposite of selecting one: it is the observability routing exists to
produce, and banning it would delete audit data to satisfy a rule about dispatch. The test
now covers the packages that *invoke* reasoning (`agents`, `dynamic_knowledge`, `workflows`,
`graph_schema_analyzer`), which are clean. A second test forbids `ai/` importing its own
deprecated shim, so the shim stays deletable.

**Verification.** Full real-infra suite **1880 passed, 2 skipped, 0 failed**. mypy 47/16
across 481 files, unchanged.

**Still open in D1, and the rest of Wave D:** `AIGatewayService.evaluate` and
`StructuredOutputInvoker.invoke` are argued in the README to share one path (route pool,
config, guards, breakers, limiters) and differ only in response contract — that claim is
prose, not a test. D2 (durable interception; the filesystem-polling `ManualFileProvider` is
still what MANUAL uses), D3 (`/api/config`) and D4 (`/api/returns`) are untouched. The
frontend's E2–E5 remain blocked on D3/D4 plus a principal endpoint.

## Wave C4 / Phase 12, slice 6 — the rebuild trigger, and one authoritative pointer

Status: DONE.

**`build_and_activate` had no caller anywhere in `src/`.** Every part of the blue/green
protocol existed and none of it was reachable, which is why production still resolves
`LEGACY_GENERATION_ID`. `lifecycle/rebuild_trigger.py` is the entry point.

The trigger is **derived, not configured**: `ActiveRuntimeSnapshot` already records the
`schema_fingerprint` and `configuration_release_id` the live generation was built from, and
`ActiveSchema` carries its own `configuration_checksum`. A rebuild is needed exactly when
those disagree; a separate "rebuild needed" flag would be a second source of truth that
could contradict the first. `ensure_current` is idempotent by contract -- it is meant to be
called on startup, on a schedule, and from an operator endpoint, and to do nothing when
nothing changed.

Two decisions worth their own note. A **release id change with byte-identical schema still
rebuilds**, because the snapshot pins the release for audit and the fingerprint alone would
call that unchanged. And **`ActivationError(stage="ACQUIRE_REBUILD_LEASE")` is not an
error**: two replicas calling this on startup is the expected case, so the loser stands
down quietly -- while every *other* activation failure propagates, since swallowing a
validation failure would make a broken rebuild indistinguishable from a busy one.

**The two parallel notions of "current generation" are reconciled.** This had been carried
as an open question across three slices, with `snapshot_activation_version=0` recorded
rather than invented. Resolution: **`ActiveRuntimeSnapshot` wins.** It is the pointer the
activation compare-and-swap moves, so resolving anything else lets a request read a
generation the cutover has already replaced. `MongoGraphStateProvider.active_generation`'s
older `dynamic_graph_generations` lookup stays as the fallback rather than being deleted,
because until a rebuild has ever run there is no snapshot -- which is still production's
state today. Activation version 0 now means precisely "resolved without a snapshot" and can
never collide with a real one (`activation_version` is `ge=1`). The real version now reaches
the lease.

**Verification.** 9 rebuild-trigger tests and 4 new handle tests covering the precedence
rule, the fallback, and a snapshot-read failure degrading rather than failing the request.

### Wave C real-infra gate

| Gate item | Covered by |
|---|---|
| Mongo source discovery | `tests/source_connectors/`, `test_on_demand_sync_production_wiring.py` |
| SQL source discovery | `tests/source_connectors/` (SQL Server connector, real instance) |
| Neo4j build N+1 | `test_lifecycle_orchestrator.py` (orchestrated build against doubles) |
| live sync | `test_generic_sync_coordinator.py`, on-demand sync real-infra tests |
| activation | `test_lifecycle_orchestrator.py`, `test_generation_validation.py` |
| old generation drain | `test_generation_drain.py`, `test_generation_lease_store_real_infra.py` |
| validation failure keeps N active | `test_generation_validation.py` (asserts the live generation is still ACTIVE *and* the snapshot did not move) |
| targeted sync then query retry | `test_on_demand_sync_production_wiring.py` |

~~**Honest limitation:** ...not by a single end-to-end run...~~ **RESOLVED.**
`tests/dynamic_knowledge/test_generation_lifecycle_e2e.py` now drives
`RebuildTrigger.ensure_current` from a real Mongo source document, through the real
`GenericSyncCoordinator` into a real Neo4j, past real deep validation, to a real
compare-and-swap, with the previous generation really draining to RETIRED. Assembly mirrors
`GraphSyncService`'s production recipe deliberately -- a test that wired the pipeline
differently could pass while production's wiring was broken.

Four cases: first build; the trigger's idempotence against a current snapshot; **build N+1**
(different generation, `activation_version` 1→2, predecessor RETIRED); and **validation
failure keeps N active** (empty source → candidate FAILED, snapshot unmoved at version 1,
generation N still ACTIVE).

**Two things the composition exposed that no unit test had.** The shared `active_schema`
fixture declares a POSTGRESQL source for which no connector exists, and deep validation
requires *every* declared label populated -- so narrowing the sync is not enough, the schema
itself must describe only what the environment can build. More importantly: seeding the
cursor field as an ISO **string** makes `scan` return zero rows while
`capture_high_watermark` still reports a plausible watermark, because MongoDB does not
compare a BSON date to a string. That is a **silent empty build**, and the only reason it
surfaced is that deep validation now rejects an unpopulated generation -- before Phase 12
slice 5 it would have activated.

## Wave C4 / Phase 12, slice 5 — deep validation

Status: DONE.

`VALIDATING -> READY_FOR_ACTIVATION` was the state transition itself, with a comment
saying real validation was out of scope. A build that projected zero nodes, or whose edges
attached to the previous generation, would activate and start serving.

`graph/validation.py` compiles checks **derived from the schema, never hand-configured** --
the discipline `constraints.py` follows, because a separately-maintained validator drifts
and then reports green on exactly the builds it no longer understands:

| Check | Severity | Why |
|---|---|---|
| `NODE_LABEL_POPULATED` | ERROR | an entity projecting zero nodes means the build lost a slice of the domain |
| `NODE_KEY_COMPLETE` | ERROR | Neo4j treats a null property as *absent* from a uniqueness constraint rather than a violation, so the constraint does not catch this and the node is unfindable by the lookup the constraint exists to serve |
| `RELATIONSHIP_ENDPOINTS_SAME_GENERATION` | ERROR | the blue/green bleed: an edge stamped with the new generation attached to an endpoint in the old one. Invisible to every other check, and it dangles once the old generation retires |
| `RELATIONSHIP_TYPE_POPULATED` | WARNING | a sparse source legitimately produces no edges of a type; failing activation on it would make the platform unable to rebuild at all |

`lifecycle/neo4j_validator.py` runs them; the Cypher is pure compilation so it is testable
without a database. Labels and relationship types are interpolated (Cypher cannot
parameterise them) and that is safe **only** because both come from `GraphIdentifier`,
pattern-constrained to `^[A-Za-z_][A-Za-z0-9_]*$` at load time; generation ids, which are
not so constrained, are always parameters.

The orchestrator's `_validate` raises on ERROR, which is what implements the Wave C gate
item "validation failure keeps N active": the candidate is marked FAILED by the slice-1
rollback and the compare-and-swap never runs. A missing validator is logged loudly rather
than passing silently -- "we validated and it was fine" and "we did not validate" must not
look the same in an incident.

**Verification.** 8 unit tests (including the gate item asserting the live generation is
still ACTIVE *and* the snapshot did not move) plus **6 real-Neo4j tests**. The real-infra
ones are not optional here: a typo in a label or property name produces valid Cypher that
counts zero and reports every generation healthy, which no unit test can catch.

**Two operational lessons recorded so they are not relearned.** Neo4j auth must come from
`GRAPH_PASSWORD` (the variable `compose.yaml` uses for `NEO4J_AUTH`) with **no default** --
a wrong guess trips Neo4j's authentication rate limiter, which then fails every Neo4j test
in the run and needs a container restart to clear. `run_real_infra_suite.sh` now sources it
from the repo `.env` and forwards it, and fails fast if it is absent. Separately: running
all six compose services plus the test runner concurrently starves Neo4j badly enough that
these six tests time out past 10 minutes; with only Neo4j and the runner up they complete
in **1.2 seconds**. Start only what a focused run needs.

## Wave C4 / Phase 12, slice 4 — REBIND_ON_RESUME (and the stale cache it exposes)

Status: DONE. Slice 4 of Phase 12; the phase is still **not** complete.

A clarification pause can last days. The paused checkpoint records the generation the turn
was reading and every graph node reads `graph_generation_id` out of that state, so a
resumed turn kept querying whatever generation it started on however many rebuilds had
happened since. That is **strict pinning arrived at by accident**, and it is the opposite
of the plan's default.

`GenerationBinding` is now a field on `AgentPolicy`, defaulting to `REBIND_ON_RESUME`, with
`STRICT_PINNING` selectable from schema config. Under strict pinning the turn leases the
*pinned* generation via the new `acquire_read_pinned` -- leasing "current" would leave the
pinned generation unprotected and free to retire while the request read it.

**The half that is easy to miss.** Rebinding the id alone is not enough and would have
passed a weaker test. `orderSearchCache` survives between turns and holds a `CandidateSet`
stamped with the generation it was built from; `CandidateSet.validate_selection` raises
"candidate set belongs to a stale graph generation" on mismatch. A rebind that kept the
cache would turn the associate's answer -- "the second one" -- into a hard error instead of
a fresh search. The rebind therefore clears the cache on resume, and `_cache_for_generation`
drops a stale cache on ordinary turns too, reading the generation off the embedded
CandidateSet rather than stamping a second copy of it.

**Verification.** 10 tests in `test_generation_rebind.py`. One is worth calling out: the
whole rebind rests on LangGraph applying `Command(resume=..., update=...)` *before* the
interrupted node re-runs. If `update` were ignored on resume, or applied and then
overwritten, the coordinator would silently keep the stale generation and every other test
here would still pass. That is now asserted against a real compiled graph, including that
the resumed node observes the rebound value. (Aside worth remembering: a `TypedDict` for a
LangGraph state must be declared at module level -- `from __future__ import annotations`
turns its hints into strings and LangGraph resolves them with `get_type_hints` against
module globals, so a function-local one dies on a bare `NameError`.)

Full real-infra suite: **1845 passed, 2 skipped, 0 failed**. mypy 47/16.

**Still open in Phase 12:** deep validation is still a bare state transition; the rebuild
trigger is unimplemented; `active_generation` reads `dynamic_graph_generations`, a notion of
"current" parallel to `ActiveRuntimeSnapshot`, and reconciling the two needs a decision on
which is authoritative; and the Wave C real-infra gate (discovery -> build N+1 -> live sync
-> activation -> drain -> validation-failure-keeps-N) has never been run end to end.

## Wave C4 / Phase 12, slice 3 — the write side reserves, and refuses a drain

Status: DONE. Slice 3 of Phase 12; the phase is still **not** complete.

On-demand sync writes into the graph and held no `GenerationWriteReservation`, so
retirement's drain could not see in-flight writes at all.

**The finding that made this more than bookkeeping.** The Neo4j write fence was assumed to
cover this. It does not. `OnDemandNeo4jGraphWriter.write` reads the generation's *current*
status via `get_status` and passes that same value as `expected_generation_status`, so
`compile_generation_fence` only rejects a status change *during* the write — a DRAINING
generation is accepted without complaint. Before this slice, on-demand sync would happily
write into a generation about to be retired, and report success for data that then
disappeared.

**So the write path's refusal policy is deliberately the opposite of the read path's.** A
refused read degrades to unleased, because serving from a generation on its way out is
merely stale. A refused write raises `GenerationDraining` (retryable) so the caller
re-resolves and writes to the successor, which is already ACTIVE. A store *outage* still
degrades to unreserved in both cases — an outage carries no information about the
generation, and refusing to write because the bookkeeping is unavailable would take the
platform down for a cleanup concern.

**The reservation is taken before the source read, not just around the graph write.**
Reserving only around the write leaves a window where retirement begins draining during
the source read, counts nothing outstanding, retires, and the write lands in something
already gone. Holding across the read only makes a drain wait slightly longer.

`synchronize` keeps the `async with` and the body moved to `_execute`, same reasoning as
`process_turn`/`_run_turn` in slice 2. `runtime_factory` shares one
`owner_instance_id` and one lease store between the coordinator and the sync coordinator,
so a read lease and a write reservation taken inside the same turn are counted against one
generation document and one drain.

**Verification.** 6 tests in `test_generation_write_reservation.py`, including a drain that
starts *mid-write* (the reservation must remain counted) and the refusal path asserting the
body never runs. Full real-infra suite: **1835 passed, 2 skipped, 0 failed**. mypy 47/16.

## Wave C4 / Phase 12, slice 2 — the request path actually takes a lease

Status: DONE. Slice 2 of Phase 12; the phase is still **not** complete.

Slice 1 built the drain. On its own that was machinery with nothing to wait for: no
request-path code acquired a `GenerationReadLease`, so `outstanding()` always returned
zero and retirement would still have removed a generation a live turn was reading. This
slice closes that.

**`dynamic_knowledge/lifecycle/handle.py`** makes "resolved but not leased" unrepresentable.
`GenerationHandleProvider.acquire_read()` is an async context manager that resolves the
generation and claims the lease as one step, and releases on exit including on failure —
there is no API that hands back a generation id without having recorded the claim, and no
way to hold the claim past the `async with`. That is Phase 12's "no code below handle
acquisition resolves 'current generation' independently", made structural rather than
advisory.

**The lease is best-effort by design.** `acquire_read` yields an unleased handle when no
store is configured, when the store errors, and when the store *refuses*. A refusal means
the generation started draining between resolution and acquisition — exactly the case the
plan answers with `REBIND_ON_RESUME`, which does not exist yet. Until it does, degrading
to the unleased behaviour that shipped before this slice beats failing a request that
would have worked. `GenerationHandle.leased` reports which happened so nothing has to
infer it.

**Wired for real.** `runtime_factory.py` now constructs `MongoGenerationLeaseStore` on
`dynamic_graph_generation_leases` and passes it to the coordinator; without that this
slice would have been inert. The collection name is a constant in `lease_store.py` rather
than at the composition root, because reader (retirement) and writer (request path)
drifting onto different collections would look exactly like a drain that always finds
nothing.

`process_turn` was split: it now owns the `async with`, and the ~150-line turn body moved
to `_run_turn`. Wrapping in place would have re-indented the whole body and made the diff
unreviewable.

**Verification.** 8 tests in `test_generation_handle.py`, the last of which is an AST-based
architecture test asserting no call to `.active_generation(...)` outside `handle.py`. It
matches calls rather than text so the protocol declaration and the Mongo implementation
(both `def`s) are not false positives. 330 passed across `tests/dynamic_knowledge` and
`tests/reasoning`.

**Deliberately not done here.** The write side: on-demand sync writes to the graph and
should hold a `GenerationWriteReservation`, which the store supports and nothing acquires.
A resumed clarification currently reuses the *original* turn's `graph_generation_id` from
the paused checkpoint — that is strict pinning, the opposite of the plan's
`REBIND_ON_RESUME` default, and changing it is a behavioural decision rather than a wiring
one. Also note `active_generation` reads the `dynamic_graph_generations` collection, a
notion of "current" parallel to `ActiveRuntimeSnapshot`; the handle records
`snapshot_activation_version=0` rather than inventing a version, and reconciling the two
is its own slice.

## Wave C4 / Phase 12, slice 1 — DRAINING, lease-aware retirement, failure rollback

Status: DONE. Slice 1 of Phase 12; the phase is **not** complete (see below).

Phase 12 asks for the full generation lifecycle. Three of its requirements were
documented-but-absent rather than merely unbuilt, which is worse — the docstrings read
as if the behaviour existed:

- `GenerationReadLease` and `GenerationWriteReservation` both promised that "cleanup of a
  RETIRED generation waits for every read lease ... to drain or expire". Neither model was
  referenced anywhere in `src/` outside its own definition. The orchestrator went
  ACTIVE → RETIRED the instant its compare-and-swap succeeded.
- `DRAINING` was not in `GraphGenerationStatus` at all.
- A build that raised anywhere after `create_generation` left its candidate parked in
  BUILDING/CATCHING_UP/VALIDATING forever, indistinguishable from a rebuild still running.
  `test_lifecycle_orchestrator.py` asserted exactly this with the comment
  `# stuck, not cleaned up`.

**Built.** `DRAINING` added to the status enum. New
`dynamic_knowledge/lifecycle/lease_store.py`: a `GenerationLeaseStore` protocol and a
Mongo implementation holding the drain flag and both lease classes in **one document per
generation**, so single-document write atomicity is the entire concurrency argument — no
transaction, no read-then-write window. Retirement is now ACTIVE → DRAINING → RETIRED,
closing the lease store to new work *before* changing the Neo4j status (the other order
leaves a window where the generation reads DRAINING but would still hand out a lease
nobody waits for), then waiting on outstanding work bounded by `drain_timeout_seconds`.
Expired leases do not count, so a crashed holder drains itself. `_retire` never raises:
it runs after the CAS, so the activation has already succeeded, and a generation left in
DRAINING is safe — unreachable, just not yet cleaned up. `build_and_activate` now rolls a
failed candidate to FAILED, best-effort, never masking the original exception, and never
from ACTIVE.

**Verification.** 9 orchestrator tests (`test_generation_drain.py`) asserting the
transition *path*, not just the destination — ACTIVE→RETIRED and ACTIVE→DRAINING→RETIRED
both end RETIRED and only one is correct — plus 7 real-Mongo tests
(`test_generation_lease_store_real_infra.py`), including a 25-iteration acquire-vs-drain
race asserting there is no third outcome: a lease is either refused, or granted **and**
counted. A granted-but-uncounted lease is the reader whose generation gets retired out
from under it, which is the whole failure this prevents.

**Also fixed, my own defect from the previous slice.**
`test_return_workflow_concurrency.py` and `test_return_workflow_rejection.py` hardcoded
`localhost:7233` instead of reading `PLATFORM_TEST_TEMPORAL_TARGET`, the convention
`tests/conftest.py` and `tests/test_order_discovery_workflow.py` already follow. They
passed on the host and failed in the real-infra container — the container run is what
caught it.

**Full real-infra suite: 1820 passed, 2 skipped, 1 failed** (21m15s). The one failure was
`test_a_conflicting_command_id_...` tripping its own 20s `asyncio.wait_for` bound under
full-suite load; the file passes in 4s in isolation, and the `wait_for` cancellation is
what produced the accompanying `UnfinishedUpdateHandlersWarning` (the test's `finally`
terminated the workflow mid-update). The bound is a *hang* detector — the failure it
guards against is infinite — so it was raised to 120s, which discriminates identically
without flaking. Not a logic defect, but recorded rather than quietly re-run: a timeout
tuned tight enough to flake is a real defect in the test.

**Still open in Phase 12** (this slice deliberately did not attempt them): deep validation
is still a bare state transition with a comment saying so; nothing in the request path
*acquires* a read lease or write reservation yet, so the drain currently has nothing real
to wait for; the rebuild trigger is unimplemented; `REBIND_ON_RESUME` vs strict pinning is
not modelled; and there is no architecture test for "no code below handle acquisition
resolves current generation independently". The Wave C real-infra gate (Mongo/SQL
discovery → Neo4j build N+1 → live sync → activation → drain → validation-failure-keeps-N)
has not been run end to end.

## Rejected stage commands no longer wedge the return session (`task_bd3a4652`)

Status: DONE.

`ReturnWorkflowTransitionError` was a plain `RuntimeError`. Temporal only *fails* a
workflow or update when the raised exception is a `FailureError`; anything else is a
**workflow task** failure, which the server retries indefinitely. So every deterministic
rejection — `STAGE_OUT_OF_ORDER`, `COMMAND_CONFLICT`, `ALREADY_COMPLETED`,
`PERSISTENCE_MISMATCH` — put the session into a permanent retry loop instead of returning
a verdict.

**The blast radius was larger than "one wedged return."** `ReturnOrchestrator.run_forever`
is a sequential claim→process→release loop, and `_complete` awaits `execute_update`. A
single rejected command therefore hung the orchestrator worker itself, which then stopped
claiming *any* further returns. What looked like a per-session defect was a worker-wide
stall.

Fix: `ReturnWorkflowTransitionError` now extends `ApplicationError` with
`type=<ReturnWorkflowErrorCode>` and `non_retryable=True` — matching the convention
`activities.py::transition_return_session` already used. Only the message and type survive
serialization, so `type` is what carries the stable code to a client; the `code` attribute
remains for in-process callers. The rejection now fails the update, the workflow stays
alive, and the orchestrator's existing `except Exception → _fail` path marks the return
FAILED with a HIGH-priority support case and moves on.

Also fixed in the same blast radius: `_fail` derived its failure code from
`type(error).__name__`, which would have stamped every distinct rejection as the opaque
`WORKFLOWUPDATEFAILEDERROR` on both the return record and the operator's support case. It
now unwraps `WorkflowUpdateFailedError` → `ApplicationError.type`.

**Verification.** `tests/test_return_workflow_rejection.py` (3 tests) against a real
Temporal server. The load-bearing assertion in each is the *last* one — that after the
rejection the workflow still answers queries and still accepts a valid command — because a
test asserting only "the update raised" would pass against a permanently poisoned
workflow. Confirmed to discriminate: with the base class reverted to `RuntimeError` both
async tests fail on their bounded timeouts (47s vs 6s, i.e. the hang reproduced), and both
pass with it restored. Full local set: 95 passed across
`-k "return_workflow or orchestrator or return_session or operations"`. mypy holds at the
47-error/16-file baseline. Ruff clean on all three changed files.

**Finding, not fixed here: the full backend quality gate does not currently pass.**
`scripts/linux/03_run_backend_quality.sh` runs `ruff check .` and `ruff format --check .`
from `backend/`; on this branch that reports **246 lint errors and 90 unformatted files**
under the pinned ruff 0.15.21. None are in code this session touched — they are
concentrated in older Phase-2 `configuration/` and `bootstrap/reconciler.py` files
(`typing.Optional`/`List` style, `W293`, unsorted imports). Recent slices have been
reporting "green" on the strength of the *changed-files* gate
(`scripts/dev/run_changed_gate.py`), which is a narrower claim than the ledger's wording
implied. Paying this down is Wave G/H scope (Phase 29, "full static integrity"); recording
it here so the discrepancy is not rediscovered as a regression.

Longer-standing items, not part of any Phase 7/C3 commit: the flagged Neo4j volume dedup
task, the pre-existing `openapi-drift`/`associate_flow.py` formatting conditions, a real
KMS-backed `EnvelopeEncryptor` (Phase 9), `ReasoningObservability` wiring into the
coordinator, the `ReturnPlatformConfiguration` ↔ `RuntimeSnapshot` configuration-system
bridge, mapping `orchestrator.py`'s real per-stage business logic onto agents, the 4-way
source-config schema reconciliation, and the `LogicalTargetedReadPlan` AND/OR condition-tree
redesign v2's full query shape would need.
