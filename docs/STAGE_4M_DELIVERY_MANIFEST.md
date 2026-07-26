# Stage 4M — Delivery Manifest

## Delivery scope

Stage 4M adds production-safe external dependency simulation to the existing production return application. It does not create a separate demo application and it does not put mock responses inside the business workflow.

## Backend additions

```text
backend/config/dependency_simulation.yaml
backend/src/return_platform/dependency_simulation/
backend/src/return_platform/api/dependency_simulator.py
backend/tests/test_dependency_simulation.py
scripts/validate_stage4m_dependency_simulation.py
```

Modified production integration points:

```text
backend/src/return_platform/configuration/settings.py
backend/src/return_platform/main.py
backend/src/return_platform/ai_gateway/providers/contracts.py
backend/src/return_platform/ai_gateway/providers/google.py
backend/src/return_platform/ai_gateway/providers/openai_compatible.py
backend/src/return_platform/workers/integration_outbox.py
scripts/run_all_host.sh
scripts/run_worker_host.sh
```

## Frontend additions

Dedicated routes and pages:

```text
/system/dependency-simulator
/system/dependency-simulator/omc
/system/dependency-simulator/parcel
/system/dependency-simulator/freight
/system/dependency-simulator/lsi
/system/dependency-simulator/ai-metrics
/system/dependency-simulator/operations/:operationId
```

Source files:

```text
frontend/src/contracts/dependencySimulator.ts
frontend/src/api/dependencySimulator.ts
frontend/src/features/dependency-simulator/
```

## Run and validation scripts

```text
scripts/start_stage4m_simulation.sh
scripts/run_stage4m_simulated_e2e.sh
scripts/run_stage4m_gates.sh
```

## Documentation

```text
README.md
docs/plans/STAGE_4M_DEPENDENCY_SIMULATION_IMPLEMENTATION_PLAN.md
docs/STAGE_4M_DEPENDENCY_SIMULATION_ARCHITECTURE.md
docs/runbooks/STAGE_4M_SIMULATED_E2E_RUNBOOK.md
docs/STAGE_4M_IMPLEMENTATION_REPORT.md
docs/STAGE_4M_FUTURE_PRODUCTION_STEPS.md
docs/STAGE_4M_DELIVERY_MANIFEST.md
```

## Key safeguards

- Lightweight AI can modify narrative wording only.
- Deterministic code owns identifiers, state, success, failure and workflow events.
- Every model attempt and fallback is measured.
- Unexpected provider exceptions are caught at the AI boundary.
- A versioned default template always exists.
- Simulation is rejected in production.
- Simulated identifiers contain `SIM`.
- Simulator responses include `X-Simulation-Mode: true`.
- RGA requires RTV product resolution.
- Parcel label creation does not imply carrier acceptance.
- Freight tender, booking and pickup remain independent states.
