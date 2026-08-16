# Troubleshooting

**Current as of 2026-08-14, commit `dcbb7dc`.**

Every entry here cost real time to diagnose. They are recorded because the symptom
points somewhere other than the cause in almost every case.

---

## Part 1 — Environment traps

These are not platform bugs. They are properties of the machines the platform runs
on, and each one has produced hours of misdirected debugging.

### Windows dynamically reserves TCP port ranges

**Symptom.** A service refuses to bind. Node reports `EACCES`; Windows reports
*"An attempt was made to access a socket in a way forbidden by its access
permissions."* The port is not in use — `netstat` shows nothing on it — and running
as administrator does not help.

**Cause.** Windows reserves dynamic TCP ranges, usually for Hyper-V / WSL, and any
port inside them is unbindable by user processes. The ranges shift between reboots.

Ranges observed on this machine:

```text
1073-1675 · 5357 · 7354-7978 · 9386-9485 · 50000-50059
```

**Neo4j's `7474` and `7687` both fell inside `7354-7978`. A dev-server port `5391`
also fell inside a reserved range.** So Neo4j appeared to fail for authentication or
configuration reasons when it had simply never been allowed to listen.

**Check:**

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

**Fix.** From an **elevated** prompt:

```powershell
net stop winnat
net start winnat
```

That releases the reservations. If elevation is unavailable, publish on ports outside
every reserved range — check first, because the ranges move.

**Do not** conclude that Neo4j credentials are wrong from a bind failure. That
misdiagnosis leads directly into the next trap.

### Never export `GRAPH_PASSWORD`, `MONGO_ROOT_*`, `MSSQL_SA_PASSWORD` or `VALKEY_PASSWORD`

**Symptom.** Spurious authentication failures against Mongo, Neo4j, Valkey or SQL
Server in tests that pass for everyone else. Then a Neo4j
`AuthenticationRateLimit` that blocks further Neo4j tests until it self-clears.

**Cause.** `load_dotenv(override=False)`. **An exported placeholder WINS over the
real `.env` value.** Exporting these substitutes a fake credential for a working one.

Verified consequences: **nine** spurious Mongo auth failures across
`tests/dynamic_knowledge/`, and a Neo4j authentication rate-limit lockout.

**Export placeholders for `NVIDIA_API_KEY` and `GOOGLE_API_KEY` only.** Any non-empty
string works — the `test_settings` fixture only populates `Settings` fields. Let
`.env` supply Mongo, Neo4j, Valkey and SQL Server.

**Never write real keys into `.env`.**

### Agent worktrees are created stale

**Symptom.** Imports fail for packages that plainly exist. Audit line anchors do not
resolve. Work is done against code that was replaced hundreds of commits ago.

**Cause.** Worktrees under `.claude/worktrees/` are **not** created at the branch
tip. Observed created at `0448d32` — **295 commits behind**, with no
`dynamic_knowledge` package at all. Multiple sessions hit this independently.

**Always, before anything else:**

```bash
git rev-parse HEAD
git log --oneline -1
```

If HEAD is behind the tip and the tree is clean, reset. **If there is uncommitted
work at a stale base, stop and report it** — do not reset over it.

### `c2-test-runner` is shared mutable state, and `docker cp` merges

**Symptom.** A green test run that cannot be attributed to any particular code.

**Cause.** The container is shared across sessions, and **`docker cp` merges rather
than replaces**. Stale files from another session survive alongside yours. One green
result has already been poisoned this way.

**Before any real-infra run in it:**

```bash
docker exec <c> rm -rf /workspace_root/backend/{src,tests,config}
# then docker cp from your tree
# then clear __pycache__
```

A green run against unknown code is worse than no run. Coordinate before using it —
if another session is mid-run, your result is unattributable.

### Orphaned Temporal executions accumulate

**Symptom.** Throughput degrades over a session of real-infra runs. Workers appear
busy with nothing.

**Cause.** Real-infra test runs leave executions behind. They accumulate.

**Fix.** Terminate executions older than an hour. Check the Temporal UI at
`http://localhost:8080` (dev-tools profile).

### Running tests from a worktree

Three requirements, and the first one fails **silently** if you miss it:

1. **`PYTHONPATH=<worktree>/backend/src`.** The host venv has `return_platform`
   installed editable against the **main checkout**. Without this you silently test
   the wrong tree. PYTHONPATH does win — verified.
2. **A `.env` at the worktree root.** `tests/conftest.py::pytest_configure` hard-fails
   without it. Copy the main checkout's; it is gitignored.
3. **Placeholder `NVIDIA_API_KEY` and `GOOGLE_API_KEY` only** — see above.

Real-infra tests **do** run on the host. Mongo `localhost:27017`
(`PLATFORM_TEST_MONGO_HOST`, `directConnection=true`), Temporal `127.0.0.1:7233`,
Neo4j `7687`, Valkey `6379`, Vault `8200`, SQL Server **`14330` on the host** and
`1433` in-network. The in-network port is the one to use from inside a container and
the commonest source of a "SQL Server is down" that is really a port mix-up.

Wall-clock in this suite is dominated by **interpreter startup and import I/O on
`K:`**, not by tests. Check `--durations` before believing a slowdown is real.

### Exercising the AI path without API keys

`ORDER_AGENT_REASONING_V1` lists `MANUAL` in `allowedProviders` — but that alone is
not enough, and this is the trap. **`MANUAL` must also be in
`PLATFORM_AI_PROVIDER_ORDER`**, or no MANUAL route is ever *built* and a keyless turn
dies with `attempts=0 last_error=PROVIDER_UNAVAILABLE` — nothing tried, because
nothing was constructible. Put it last, so a deployment holding a real credential
never reaches a human:

```bash
PLATFORM_AI_PROVIDER_ORDER=GOOGLE,NVIDIA,SIMULATOR,MANUAL
```

Where the human answers is `PLATFORM_AI_MANUAL_HANDOFF`:

* **`UI`** — the durable interception store, answered in the AI Control Center's
  interceptions tab (`GET /api/ai/interceptions`, then `…/{id}/request` to unseal and
  `…/{id}/answer` to paste the JSON). Requires an interception store; refused outright
  rather than downgraded if the process has none.
* **`FILE`** — `ManualFileProvider` writes the request to `.manual_llm/requests/` and
  waits for a reply in `.manual_llm/responses/`. The directory is **not configurable**
  — always `.manual_llm` relative to CWD. Patch the module global in a test. See
  `tests/test_manual_provider_reasoning_e2e.py` and
  `backend/scripts/manual_llm_responder.py`.
* **`AUTO`** (default) — `UI` when the process has a store, `FILE` otherwise. The
  order-discovery worker always has one.

Raise `PLATFORM_AI_TIMEOUT_SECONDS` (the repository ships 280) — it, not the
provider's own 600 s hold, bounds how long the operator actually has.

Full walkthrough and the record/replay alternative:
`docs/RETURN_COPILOT_EXECUTION_STATE.md` → "Running the reasoning path".

### Contract drift

```bash
python scripts/check_openapi_drift.py            # verify
python scripts/check_openapi_drift.py --write    # regenerate the five artifacts
```

It **is wired into pytest** (`backend/tests/test_openapi_contract_drift.py`), so a
contract change that is not regenerated fails the suite rather than shipping
silently. Earlier guidance said it was not wired in; that is no longer true.

---

## Part 2 — Platform symptoms

### The copilot finds nothing, and everything is healthy

**The single most confusing state this platform has.** Every service up, every health
check green, discovery truthfully reporting no matches.

**Cause.** The graph is empty. Loading the source collections leaves Neo4j with no
nodes.

```bash
python backend/scripts/build_knowledge_graph.py
```

Or check whether a sync has ever run: `GET /api/graph-sync/runs`.

It reads as a broken agent rather than a missing build, which is why it belongs at
the top of this section.

### A configuration change had no effect

**Cause.** The release is `ACTIVATING`, not `LIVE`. Some process class has not
adopted it.

```bash
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq
```

`LIVE` requires every required class to report the activated release id **and** head
revision. A process on the right release at an older head revision has not adopted.

On a host deployment, `scripts/linux/09_start_workers.sh` starts **no**
order-discovery worker, so adoption stays `ACTIVATING` indefinitely until one is
started from `backend/scripts/run_order_discovery_worker.py`. See
[`startup.md`](startup.md) — known issue.

Also: `/health/ready` does **not** report adoption. A process can be ready and
serving the previous release.

### `Login failed for user 'sa'` under load

**Not a credential problem.** It is SQL Server refusing connections because the
platform opened more than it will accept, and the real cause is only visible in the
server's own log.

Every instinct the message triggers — check the password, check Vault, check the
connection string — is wrong.

Check `sqlserver_pool_max_size` (default 8) against replica count:
`max_size × (API replicas + worker replicas)` must stay under the server's limit with
headroom for migrations, probes and administrative sessions. See
[`../optimization/connection-pooling.md`](../optimization/connection-pooling.md).

### A shipment update returned 502

The SQL row **committed**; the graph projection did not.

**Resubmit the identical update.** It is idempotent and answers `DUPLICATE`. Do not
re-enter the data — a second distinct observation is a second observation.

### Support replied and nothing happened

Historically: the case had no running workflow, so the signal went into a namespace
with no such execution and was lost to a `NOT_FOUND`.

Now: check `workflowId` on the case. If null, the recovery sweep will start it —
confirm the sweep is running. See [`recovery.md`](recovery.md).

### A case chased Support overnight

Historically: wall-clock arithmetic. A return raised at 16:30 on a Friday chased at
18:30, 20:30 and 22:30 into an empty queue and parked at 00:30 on Saturday.

Now the arithmetic runs against `business_calendar_id`. If it still happens, the
configured calendar declares those hours as working, or `business_calendars` is
**empty** — which is not a silent Mon–Fri: `resolve_business_deadline` falls back to
wall clock **and says so on the case**.

### Bay is always pending

Historically: `request_bay_assignment` wrote one fact and returned. It queried no
graph, ranked no bay and resolved no location, and the workflow waited out
`bay_wait_seconds` every time for a `bay_result` signal whose only sender was a test.

Now check the recommendation's own `reason` and `explanation` — they are populated on
every outcome. `ABSENT / NO_WAREHOUSE_REFERENCE` means the case's fact projection and
the confirmed order's shipping warehouse both failed to resolve one, which is a real
answer distinct from "this warehouse has no eligible bay".

`capacity_evidence: DECLARED` means the live reservation aggregate could not be read,
so the chosen bay may already be full and the reservation may refuse it.

### Bay confidence is missing

`confidence_millionths` is `None` **only when no recommendation was produced at all**.
That is different from a recommendation made with low confidence, and the two must not
be read as the same thing. It is never a constant — it is the computed margin of the
winner over the runner-up.

### Vault token file missing

```bash
./scripts/infra.sh start
python3.13 scripts/vault/bootstrap_local_vault.py
ls -l .vault-local/return-platform.token
```

### Graph configuration release missing

```bash
./scripts/prepare_runtime_configuration.sh
```

### Neo4j index not online

```bash
python3.13 scripts/apply_neo4j_migrations.py
```

An index that **exists but is still populating** returns incomplete results, which is
why bootstrap checks for `ONLINE` rather than existence — an incomplete fuzzy search
is exactly the defect the complete-corpus invariant exists to prevent.

### SQL Server schema not current

```bash
python3.13 scripts/apply_sql_migrations.py
```

Applies any packaged
`backend/src/return_platform/configuration/sql_migrations/NNN_*.sql` not yet recorded
in `platform.schema_migrations`. Safe to rerun.

### Phone or email lookup stopped after HMAC rotation

The key is intentionally non-recoverable from graph evidence — that is what makes the
evidence safe to store. Existing evidence **cannot be recomputed in place**. Rebuild
the customer projection with the current Vault key, validate graph freshness, then
re-enable contact lookup.

### The containerized build fails on certificates

```text
unable to get local issuer certificate
```

The container trusts nothing your proxy signs, even where your host does:

```bash
EXTRA_CA_CERTS="$(cat /path/to/corp-root.pem)" ./scripts/infra.sh full-containerized
```

A root certificate is public by design, so a build argument is the right shape — the
private key is the secret, and that is not this.

### Asking for infrastructure builds the whole backend image

Fixed, but worth knowing the shape: `infra.sh start` used to be a bare
`docker compose up -d`, which brought up `runtime-configuration-init` — built from
`return-platform-backend:local`. Behind a TLS-intercepting proxy it failed outright.
The script now names its services. If you see an image build during
`infra.sh start`, something has regressed.

### Frontend Node version rejected

Use Node 24 and npm 11, then rerun `./scripts/bootstrap_host.sh`.

### Playwright browser missing

```bash
cd frontend && npx playwright install --with-deps chromium
```

### Dependency simulation refused in production

By design, and it fails closed. The guard string is in
`configuration/settings.py`: *"External dependency simulation is forbidden in
production."*

Note the apparent contradiction that is not one: the `DEPENDENCY_SIMULATION` domain
must be **present and valid** in the release even though simulation is off, so the
platform can prove it is off rather than inferring it from an absent block.

---

## Diagnostic order

Cheapest first, and stop when the answer appears:

1. the failing request's **correlation id** — `meta.request_id`, echoed from
   `X-Correlation-ID`;
2. the relevant log window for that id;
3. `/health/ready`, then `/api/config/adoption` — **both**, they answer different
   questions;
4. the relevant screen's own error text, which names what it could not do;
5. the relevant symbol;
6. wider logs, only if still unresolved.

Do not dump whole service logs first. The correlation id is in every record for a
reason.

## Related

- [`startup.md`](startup.md) · [`shutdown.md`](shutdown.md) ·
  [`reset.md`](reset.md) · [`recovery.md`](recovery.md)
- [`../architecture/security-boundaries.md`](../architecture/security-boundaries.md)
