# Stage 4N — AI Simulator E2E Runbook

## Purpose

Validate AI route selection, model/key rotation, safety, exact output schemas, usage metrics, and fallback behavior without calling live AI providers.

## Prerequisites

```bash
python3 --version
```

Python 3.13 is the supported project runtime. Install project dependencies through Poetry for the full gate suite.

## Dependency-light AI E2E

```bash
./scripts/run_stage4n_ai_simulator_e2e.sh
```

This executes scripted lightweight providers and validates:

```text
list-backed credentials and models
lightweight/standard task isolation
credential failover
model failover
prompt-injection blocking
domain-only behavior
exact simulator response schema
AI usage metrics
deterministic fallback
business-operation continuity
```

Evidence is written to:

```text
docs/evidence/stage4n_ai_gateway/validation_summary.json
```

## Focused AI tests

```bash
./scripts/run_stage4n_ai_tests.sh
```

Direct command:

```bash
PYTHONPATH=backend/src pytest --noconftest -q \
  backend/tests/test_ai_gateway_policy.py \
  backend/tests/test_ai_gateway_routing.py \
  backend/tests/test_dependency_simulation.py
```

## Full source gates

```bash
./scripts/run_stage4n_full_gates.sh
```

This adds Stage 4M dependency validation, frontend syntax validation, source/contract gates, and shell syntax validation.

## Live-stack E2E

Create `.env`:

```bash
cp .env.example .env
```

Configure key and model lists when live AI is desired:

```env
PLATFORM_GOOGLE_API_KEYS=["replace-me"]
PLATFORM_GOOGLE_LIGHTWEIGHT_MODELS=["configured-lightweight-model"]
PLATFORM_GOOGLE_STANDARD_MODELS=["configured-standard-model"]

PLATFORM_NVIDIA_API_KEYS=[]
PLATFORM_NVIDIA_LIGHTWEIGHT_MODELS=[]
PLATFORM_NVIDIA_STANDARD_MODELS=[]
```

Start the simulated dependency stack:

```bash
./scripts/start_stage4m_simulation.sh
```

In another terminal:

```bash
./scripts/run_stage4n_live_stack_e2e.sh
```

This runs the production-v2 branch parcel scenario with simulated external dependencies and inspects:

- route health;
- task policies;
- central AI usage summary;
- dependency-simulator AI summary.

When no live AI route is configured, the dependency simulator uses its deterministic template and the return flow must still fully close.

## Dedicated pages

```text
http://127.0.0.1:5173/ai-gateway/routes
http://127.0.0.1:5173/ai-gateway/tasks
http://127.0.0.1:5173/ai-gateway/metrics
http://127.0.0.1:5173/ai-gateway/safety
http://127.0.0.1:5173/system/dependency-simulator/ai-metrics
```

## Expected fallback result

An AI outage must produce:

```text
simulated dependency operation = CONFIRMED
narrative source = DEFAULT_TEMPLATE
AI metrics = FAILED + FALLBACK
workflow progression = unaffected
```

## Production safety

- Dependency simulation is forbidden when `PLATFORM_ENVIRONMENT=production`.
- The AI safety-test endpoint is disabled outside development/test.
- Raw credentials never appear in the UI or metrics.
- A model response never directly performs an authoritative return action.
