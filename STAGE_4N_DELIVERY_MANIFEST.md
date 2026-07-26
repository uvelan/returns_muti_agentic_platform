# Stage 4N — Delivery Manifest

## Primary package

```text
returns_muti_agentic_platform_stage4n_ai_gateway.zip
```

## Configuration

```text
.env.example
backend/config/ai_gateway.yaml
backend/config/dependency_simulation.yaml
```

## Backend

```text
backend/src/return_platform/configuration/settings.py
backend/src/return_platform/ai_gateway/configuration.py
backend/src/return_platform/ai_gateway/routing.py
backend/src/return_platform/ai_gateway/safety.py
backend/src/return_platform/ai_gateway/models.py
backend/src/return_platform/ai_gateway/service.py
backend/src/return_platform/ai_gateway/providers/http.py
backend/src/return_platform/api/ai_gateway.py
backend/src/return_platform/dependency_simulation/ai.py
backend/src/return_platform/dependency_simulation/configuration.py
backend/src/return_platform/dependency_simulation/models.py
backend/src/return_platform/dependency_simulation/service.py
backend/src/return_platform/api/dependency_simulator.py
backend/src/return_platform/operations/models.py
backend/src/return_platform/operations/repository.py
backend/src/return_platform/main.py
```

## Frontend

```text
frontend/src/contracts/operations.ts
frontend/src/contracts/dependencySimulator.ts
frontend/src/api/operations.ts
frontend/src/features/operations/AIGatewayPages.tsx
frontend/src/features/dependency-simulator/AiMetricsPage.tsx
frontend/src/features/ai-gateway/RouteHealthPage.tsx
frontend/src/features/ai-gateway/TaskPoliciesPage.tsx
frontend/src/features/ai-gateway/UsageMetricsPage.tsx
frontend/src/features/ai-gateway/SafetyTestPage.tsx
frontend/src/routes.ts
```

## Tests and validators

```text
backend/tests/test_ai_gateway_policy.py
backend/tests/test_ai_gateway_routing.py
backend/tests/test_dependency_simulation.py
scripts/validate_stage4n_ai_gateway.py
scripts/run_stage4n_ai_tests.sh
scripts/run_stage4n_ai_simulator_e2e.sh
scripts/run_stage4n_full_gates.sh
scripts/run_stage4n_live_stack_e2e.sh
```

## Documentation and evidence

```text
README.md
docs/implementation/STAGE_4N_AI_GATEWAY_HARDENING.md
docs/runbooks/STAGE_4N_AI_SIMULATOR_E2E.md
docs/evidence/stage4n_ai_gateway/validation_summary.json
docs/evidence/stage4n_ai_gateway/focused_pytest.log
docs/evidence/stage4n_ai_gateway/simulator_ai_e2e.log
docs/evidence/stage4n_ai_gateway/frontend_syntax.log
STAGE_4N_IMPLEMENTATION_REPORT.md
STAGE_4N_FUTURE_PRODUCTION_STEPS.md
STAGE_4N_DELIVERY_MANIFEST.md
```

## Validation result

```text
Focused AI and simulator tests: 21 passed
Stage 4N validator: 9 checks passed
Simulator-focused AI E2E: 5 passed
Frontend syntax: 166 files passed
Stage 4 source gate: passed
Stage 4 contract gate: passed
Classification: SOURCE_VALIDATED
```

## Exclusions

The ZIP intentionally excludes:

```text
.env
API keys and credentials
.git
node_modules
virtual environments
Python bytecode and caches
pytest caches
coverage files
frontend build output
runtime logs outside committed evidence
```
