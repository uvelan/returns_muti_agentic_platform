# Stage 4M — Simulated External Dependencies E2E Runbook

## Prerequisites

- Linux
- Docker and Docker Compose
- Python 3.13
- Poetry
- Node.js 24 and npm 11
- root `.env` created from `.env.example`

## Configure simulation

```bash
cp .env.example .env
chmod 600 .env
```

Replace every placeholder in `.env`. Do not append duplicate assignments. Keep
`PLATFORM_ENVIRONMENT=development` and all four dependency modes set to
`SIMULATED`. JSON key/model arrays must be wrapped in single quotes.

Google and NVIDIA keys are optional for simulator-only flow execution. Without
them, simulator AI operations use deterministic fallback templates. The full
Linux validation gate additionally performs live catalog and minimal-generation
checks, so both configured provider pools must be populated for that gate.

## Install host dependencies

```bash
./scripts/bootstrap_host.sh
```

## Start all services

```bash
./scripts/start_stage4m_simulation.sh
```

The script starts Docker infrastructure, the API, Temporal worker, return orchestrator, outbox publisher, integration-outbox worker, job worker and frontend.

## Execute API-driven E2E

In another terminal, run all six paths:

```bash
for scenario in \
  BRANCH_PARCEL \
  OFFSITE_HEAVY \
  BRANCH_LTL \
  OFFSITE_PARCEL \
  DIRECT_VENDOR \
  NO_PHYSICAL_RETURN
do
  ./scripts/run_stage4m_simulated_e2e.sh "$scenario" || exit 1
done
```

Each run creates a production-v2 return session, executes the required simulated
dependency lifecycle, queries Temporal state, checks simulator headers, and
fails unless `caseFullyClosed` is `true`.

## Dedicated pages

```text
http://localhost:5173/system/dependency-simulator
http://localhost:5173/system/dependency-simulator/omc
http://localhost:5173/system/dependency-simulator/parcel
http://localhost:5173/system/dependency-simulator/freight
http://localhost:5173/system/dependency-simulator/lsi
http://localhost:5173/system/dependency-simulator/ai-metrics
```

## Validation

```bash
./scripts/linux/run_full_linux_validation.sh --from-start
```

The first full run creates a commit-bound 23-screen manual attestation and stops.
Complete that file, then run `./scripts/linux/run_full_linux_validation.sh --resume`.

## Stop infrastructure

```bash
./scripts/infra.sh stop
```
