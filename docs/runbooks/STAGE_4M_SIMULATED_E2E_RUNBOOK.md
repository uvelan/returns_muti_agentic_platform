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

cat >> .env <<'EOF'
PLATFORM_ENVIRONMENT=development
PLATFORM_OMC_DEPENDENCY_MODE=SIMULATED
PLATFORM_PARCEL_DEPENDENCY_MODE=SIMULATED
PLATFORM_FREIGHT_DEPENDENCY_MODE=SIMULATED
PLATFORM_LSI_DEPENDENCY_MODE=SIMULATED
EOF
```

Google and NVIDIA keys are optional. Without them, every simulator operation receives its default template and continues normally.

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

In another terminal:

```bash
./scripts/run_stage4m_simulated_e2e.sh BRANCH_PARCEL
```

or:

```bash
./scripts/run_stage4m_simulated_e2e.sh OFFSITE_HEAVY
```

The script creates a production-v2 return session, runs the external dependency simulation, queries Temporal state, checks that simulator headers are present and prints all generated operation IDs.

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
./scripts/run_stage4m_gates.sh
```

## Stop infrastructure

```bash
./scripts/infra.sh stop
```
