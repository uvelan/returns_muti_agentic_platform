# Stage 4N — AI Gateway Hardening Implementation Report

## Delivery status

```text
Implementation: COMPLETE
Dependency-light simulator AI E2E: PASSED
Focused tests: 21 PASSED
Frontend syntax: PASSED — 166 TypeScript/TSX files
Stage 4 source validation: PASSED
Stage 4 contract validation: PASSED
Classification: SOURCE_VALIDATED
Live provider and full-stack validation: PENDING
```

## Implemented scope

### Credential and model pools

The gateway now accepts provider credentials and model names as lists. It expands each provider/model/credential combination into a separately observable route. Raw credentials remain secret; metrics and APIs expose only a safe credential ID.

Supported lists:

```text
Google keys, lightweight models, standard models
NVIDIA keys, lightweight models, standard models
OpenAI keys, lightweight models, standard models
Anthropic keys, lightweight models, standard models
Ollama lightweight and standard models
```

Legacy single values are retained only for migration compatibility.

### Deterministic task complexity

`backend/config/ai_gateway.yaml` assigns every registered task to `LIGHTWEIGHT` or `STANDARD`.

Lightweight examples:

- eligibility;
- smart questions;
- status summaries;
- customer notification drafts;
- dependency simulator narratives.

Standard examples:

- order-candidate conflict analysis;
- Support case analysis;
- external-state reconciliation;
- feedback recommendations.

The simulator is lightweight-only and cannot escalate tiers.

### Health-aware routing

Implemented routing state includes:

- provider/model/key route identity;
- provider, model, credential, and route circuits;
- application/tier/provider/model/credential/route minute counters;
- tier/provider concurrency;
- credential cooldown after authentication or rate-limit errors;
- model isolation after model-unavailable/context errors;
- provider failover after repeated provider errors;
- bounded attempts, deadline, retry, and jitter.

### AI safety

Implemented deterministic controls for:

- input allowlists;
- sensitive-field blocking;
- depth, size, collection, and token limits;
- prompt-injection indicators;
- hidden-prompt and credential requests;
- role and human-approval bypass attempts;
- direct SQL and unauthorized authoritative-action requests;
- out-of-domain requests;
- output safety checks;
- exact JSON output schemas;
- custom-prompt restrictions outside development/test.

### Fallback behavior

Provider outage, no configured route, authentication failure, rate limit, timeout, unexpected exception, invalid JSON, invalid output schema, unsafe output, and empty output all terminate in a versioned deterministic fallback or manual-review result.

For the dependency simulator, fallback is non-blocking:

```text
deterministic dependency operation succeeds
→ AI narrative fails
→ default template is persisted
→ usage/failure/fallback metrics are persisted
→ workflow continues
```

### Metrics

Central AI attempts store:

- task ID and prompt version;
- configured and selected tier;
- provider, model, credential safe ID, route ID;
- attempt and selection reason;
- rate-limit wait and latency;
- token usage;
- estimated cost field;
- request/response digests;
- safety status;
- fallback and failure status.

Dependency-simulator AI metrics now include key, route, model tier, selection reason, token usage, cost field, digests, and fallback status.

### Backend endpoints

```text
GET  /api/v1/ai-gateway/routes
GET  /api/v1/ai-gateway/tasks
GET  /api/v1/ai-gateway/metrics
GET  /api/v1/ai-gateway/metrics/summary
POST /api/v1/ai-gateway/safety-test
```

Existing request, simulator, replay, compare, and interception APIs remain available.

### Dedicated frontend pages

```text
/ai-gateway/requests
/ai-gateway/routes
/ai-gateway/tasks
/ai-gateway/metrics
/ai-gateway/safety
/system/dependency-simulator/ai-metrics
```

The pages are separate, not combined into one dashboard.

### Scripts

```text
scripts/validate_stage4n_ai_gateway.py
scripts/run_stage4n_ai_tests.sh
scripts/run_stage4n_ai_simulator_e2e.sh
scripts/run_stage4n_full_gates.sh
scripts/run_stage4n_live_stack_e2e.sh
```

## Simulator AI validation

The Stage 4N validator uses scripted provider adapters so it can exercise the real gateway route pool, safety policy, exact schema parser, dependency simulation service, metrics repository, and fallback path without a network call.

Validated scenarios:

1. Two credentials and three models expand to six provider routes.
2. Lightweight and standard tasks cannot cross tiers.
3. A rate-limited key rotates to the next key.
4. An unavailable model rotates to the next model.
5. Prompt injection is blocked.
6. Medical/general questions are rejected as out of domain.
7. A valid lightweight simulator response is accepted.
8. Provider/model/key/route/token metrics are captured.
9. Provider failure uses a deterministic template.
10. An invalid response with extra fields is rejected.
11. RMA and parcel simulation remain confirmed when AI fails.

Evidence:

```text
docs/evidence/stage4n_ai_gateway/validation_summary.json
docs/evidence/stage4n_ai_gateway/focused_pytest.log
docs/evidence/stage4n_ai_gateway/simulator_ai_e2e.log
docs/evidence/stage4n_ai_gateway/frontend_syntax.log
```

## Validation commands and results

```bash
PYTHONPATH=backend/src pytest --noconftest -q \
  backend/tests/test_ai_gateway_policy.py \
  backend/tests/test_ai_gateway_routing.py \
  backend/tests/test_dependency_simulation.py
```

Result:

```text
21 passed
```

```bash
./scripts/run_stage4n_ai_simulator_e2e.sh
```

Result:

```text
9 validator checks passed
5 simulator-focused tests passed
```

```bash
./scripts/run_stage4n_full_gates.sh
```

Result:

```text
Stage 4N AI tests passed
Stage 4M dependency validation passed
Frontend syntax passed — 166 files
Stage 4 source validation passed
Stage 4 contract validation passed
Shell syntax passed
```

## Limitations

The packaging environment does not provide Docker, installed Node dependencies, Ruff, mypy, or live Google/NVIDIA credentials. Therefore this delivery does not claim:

- live provider validation;
- distributed Valkey rate-limit validation;
- full backend pytest suite;
- Ruff or strict mypy;
- frontend dependency-backed typecheck/lint/build/Vitest/Playwright/accessibility;
- Docker-backed production return E2E.

The application includes scripts for the supported Linux environment to run those gates.
