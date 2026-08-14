# Startup

**Current as of 2026-08-14, commit `dcbb7dc`.**

The application runs Python and React as **host processes** by default.
Infrastructure runs in Docker Compose.

## Prerequisites

- Python 3.13 · Node.js 24 · npm 11
- Docker Engine with the Compose plugin
- `flock` (from `util-linux`) on Linux
- Git
- Enough RAM for SQL Server, Neo4j, MongoDB, Temporal, PostgreSQL, Valkey and
  Vault simultaneously

```bash
python3.13 --version && node --version && npm --version
docker --version && docker compose version && flock --version
```

## First-time setup

```bash
./scripts/bootstrap_host.sh
```

Windows: `scripts/bootstrap_host.ps1`.

It checks toolchain versions; creates `.env` from `.env.example` when absent;
**upgrades** an existing `.env` by appending missing non-secret Vault references
without changing existing values; generates missing or placeholder local
infrastructure credentials **without printing them**; generates the MongoDB
replica-set key when required; and installs backend and frontend dependencies.

Generated infrastructure credentials initialize the local services and are copied
into Vault. Runtime processes then use **Vault references**, not `.env`
credentials.

**Never commit** `.env`, `.vault-local/`, generated tokens, unseal material, or
credentials.

## The startup sequence, in order

```text
1. infrastructure          ./scripts/infra.sh start
2. runtime configuration   scripts/prepare_runtime_configuration.sh  (automatic)
3. application             ./scripts/run_all_host.sh
```

### 1. Infrastructure

```bash
./scripts/infra.sh start
```

Starts Vault, SQL Server, the MongoDB replica set, Neo4j, Valkey, Temporal
PostgreSQL and Temporal. Also initializes and unseals Vault and stores local
infrastructure credentials under the approved production Vault paths.

**Datastores only — no application image is built.** That is worth stating because
it was not always true. `infra.sh start` used to be a bare `docker compose up -d`,
which also brought up `runtime-configuration-init`. That container is built from
`return-platform-backend:local`, so **asking for infrastructure built the entire
backend image first**, on a machine whose backend was about to run on the host
anyway. Behind a TLS-intercepting corporate proxy it did not merely waste time, it
failed. The script now names its services.

Nothing is skipped: the SQL migrations, Neo4j migrations and graph-configuration
bootstrap that container ran are all run on the host by
`scripts/prepare_runtime_configuration.sh`, which every host launcher invokes before
the backend starts.

Temporal UI is behind the `dev-tools` profile and does not start here:

```bash
docker compose --profile dev-tools up -d temporal-ui
```

### 2. Runtime configuration

Automatic. Each backend or worker launcher runs
`scripts/prepare_runtime_configuration.sh` unless `PLATFORM_SKIP_RUNTIME_PREPARE=true`
is explicitly supplied by the aggregate launcher.

It applies checksum-tracked Neo4j migrations, applies SQL migrations, and publishes
and validates the initial graph configuration **only when no active release
exists**. Normal restarts reuse the active release and its Vault references, and
**do not** rerun live AI provider/model validation.

The per-process sequence:

```text
load version-controlled baseline schema
  → resolve bootstrap credentials from Vault
  → connect to Neo4j
  → load the active ConfigurationHead release
  → verify release checksum
  → validate the complete configuration model
  → resolve graph-declared Vault references
  → create the immutable process snapshot
  → initialize dependency clients
```

A checksum mismatch **refuses startup**. It does not warn.

### 3. Application

```bash
./scripts/run_all_host.sh
```

Before starting processes it stops previously managed application processes; closes
repository-owned listeners on ports `8000` and `5173` while **refusing to terminate
unrelated processes**; serializes initialization with `flock`; verifies Vault
access; applies Neo4j migrations; publishes initial configuration if none is
active; then starts the API, the workers and the frontend.

To run live provider and model validation once before startup:

```bash
./scripts/run_all_host.sh --validate-ai
```

### Individual processes

```bash
./scripts/run_backend_host.sh
./scripts/run_worker_host.sh temporal            # return-workflow-worker
./scripts/run_worker_host.sh orchestrator        # return-orchestrator
./scripts/run_worker_host.sh outbox              # outbox-publisher
./scripts/run_worker_host.sh integration-outbox  # integration-outbox-worker
./scripts/run_frontend_host.sh
```

## The five worker classes

| Launcher argument | Process class | Entry point |
|---|---|---|
| `temporal` | `return-workflow-worker` | `backend/scripts/run_return_workflow_worker.py` |
| — *(see below)* | `order-discovery-worker` | `backend/scripts/run_order_discovery_worker.py` |
| `orchestrator` | `return-orchestrator` | `backend/scripts/run_return_orchestrator.py` |
| `outbox` | `outbox-publisher` | `backend/scripts/run_outbox_publisher.py` |
| `integration-outbox` | `integration-outbox-worker` | `return_platform.workers.integration_outbox` |

Plus the `api` process. Those six are `REQUIRED_PROCESS_CLASSES` — the set a
release must reach before `GET /api/config/adoption` reports `LIVE`.

`data-job-worker` **no longer exists.** It was deployed by `compose.yaml` and could
never start: `scripts/run_data_job_worker.py` imported
`return_platform.data_console`, which does not exist in this repository. It is
deliberately absent from `REQUIRED_PROCESS_CLASSES` for the same reason — listing a
class that can never report would make every release permanently not-live and turn
a real signal into one operators learn to ignore.

### Known issue — `jobs` in the host launchers

`scripts/linux/09_start_workers.sh` still iterates over a `jobs` worker and
`scripts/linux/11_validate_host_processes.sh` still checks for `worker-jobs`.
`run_worker_host.sh` has no `jobs` case and exits `2`, so a full
`./scripts/run_all_host.sh` leaves a dead `worker-jobs` and the host-process
validation step fails on it.

`run_worker_host.sh` also advertises `jobs` in two of its three usage strings while
its own case statement rejects it.

Workaround: start the four supported workers individually, or ignore the
`worker-jobs` failure in step 11. **Do not** document or script `run_worker_host.sh
jobs`.

`09_start_workers.sh` also starts **no** order-discovery worker, so
`GET /api/config/adoption` reports `ACTIVATING` indefinitely on a host deployment
until one is started from `backend/scripts/run_order_discovery_worker.py`.

## Verifying startup

```bash
curl -fsS http://127.0.0.1:8000/health/live  | jq
curl -fsS http://127.0.0.1:8000/health/ready | jq
./scripts/infra.sh status
./scripts/linux/12_validate_worker_heartbeats.sh
./scripts/linux/13_run_api_probes.sh
```

Then — and this is the step most often skipped:

```bash
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq
```

`ready` does **not** report configuration adoption. A process can be ready and
serving the previous release. Conflating them would make a process on the wrong
configuration look healthy.

## Host URLs

| Service | URL |
|---|---|
| Frontend | `http://localhost:5173` |
| Backend | `http://localhost:8000` |
| Readiness | `http://localhost:8000/health/ready` |
| OpenAPI | `http://localhost:8000/openapi.json` |
| API docs | `http://localhost:8000/docs` |
| Neo4j Browser | `http://localhost:7474` |
| Temporal UI | `http://localhost:8080` (dev-tools profile) |
| Vault API | `http://127.0.0.1:8200` |

SQL Server is `14330` on the host and `1433` in-network. Temporal is
`127.0.0.1:7233`, Neo4j `7687`, Valkey `6379`, Vault `8200`, Mongo `27017`.

**On Windows, several of these ports may be unavailable through no fault of the
platform.** See [`troubleshooting.md`](troubleshooting.md) — Windows dynamically
reserves TCP ranges, and Neo4j's `7474`/`7687` have both fallen inside them.

## Redeploy after source changes

Rebuild and restart the application without rerunning infrastructure bootstrap,
graph publication, seed data or AI validation:

```bash
./scripts/linux/redeploy_app.sh
./scripts/linux/redeploy_app.sh --install-dependencies   # if a lockfile changed
./scripts/linux/redeploy_app.sh --skip-frontend-build    # restart only
./scripts/linux/redeploy_app.sh --validate-ai            # with live AI validation
```

## Fully containerized mode

```bash
./scripts/infra.sh full-containerized
```

**The only path that builds the backend image.** On a network that terminates TLS
in the middle, the build fails on `unable to get local issuer certificate` — the
container trusts nothing your proxy signs, even where your host does:

```bash
EXTRA_CA_CERTS="$(cat /path/to/corp-root.pem)" ./scripts/infra.sh full-containerized
```

The same argument works with `docker build --build-arg EXTRA_CA_CERTS=…`. A root
certificate is public by design, so a build argument is the right shape for it — the
private key is the secret, and that is not this.

Compose order:

```text
infrastructure health → Vault init → Neo4j migrations
  → graph configuration publication → seed init → backend and workers → frontend
```

Direct Compose use requires the profile:

```bash
docker compose --profile containerized-app build
docker compose --profile containerized-app up -d
docker compose --profile containerized-app ps
```

Two images are built: `return-platform-backend:local` (shared by API, workers and
init jobs) and `return-platform-frontend:local`.

Containerized URLs are on port `3000`: `/returns`, `/support`, `/config`,
`/approvals`, `/data-sources`, `/graph-schema`, `/ai`, `/sync`, `/operations`.

## Startup AI validation

Normal startup and redeployment **do not** contact live AI providers or validate
credentials and models. Runtime preparation reuses the active release. Pass
`--validate-ai` to run live validation explicitly.

During runtime preparation, missing keys in `.env` are copied from `.env.example`
using their version-controlled defaults. Existing values are preserved and secrets
are not printed.

## Related

- [`shutdown.md`](shutdown.md)
- [`reset.md`](reset.md)
- [`recovery.md`](recovery.md)
- [`troubleshooting.md`](troubleshooting.md)
- [`../architecture/configuration-adoption.md`](../architecture/configuration-adoption.md)
