# Stage 4M — Dependency Simulation Architecture

## Runtime pattern

```text
Return business workflow
        |
        v
stable dependency gateway / transactional outbox
        |
        +-- REAL ------> approved external adapter
        +-- SIMULATED -> deterministic dependency simulator
        +-- MANUAL ----> operator evidence queue
        +-- BLOCKED ---> BLOCKED_EXTERNAL_DEPENDENCY
```

The business workflow does not contain hardcoded mock responses. Environment configuration selects the adapter.

## Simulator modules

```text
return_platform/dependency_simulation/
  configuration.py       validated YAML registry
  models.py              strict API/persistence contracts
  repository.py          MongoDB and focused-test repositories
  service.py             deterministic service state machines
  ai.py                  lightweight AI wording and fallback metrics
  templates.py           guaranteed default responses
  workflow_bridge.py     verified events to Temporal
  dispatchers.py         transactional-outbox integration
```

## Lightweight AI policy

The version-controlled default order is:

```text
GOOGLE  -> gemini-3.1-flash-lite
NVIDIA  -> meta/llama-3.2-3b-instruct
TEMPLATE -> dependency-simulator-v1
```

Provider names and models are configuration, not domain rules. The simulator first computes the final deterministic operation result. It then asks a lightweight model to produce only:

```json
{
  "message": "...",
  "summary": "...",
  "nextAction": "..."
}
```

Any timeout, missing key, unavailable model, rate limit, invalid JSON or schema violation results in the versioned deterministic template. The operation stays successful when its deterministic state machine succeeded.

## Metrics

Every attempt is written to `dependency_simulation_ai_metrics` with:

```text
operationId
sessionId
dependency
operation
provider
model
status
fallbackUsed
attempt
latencyMs
inputTokens
outputTokens
totalTokens
estimatedCostMicrousd
requestDigest
responseDigest
errorCode
createdAt
```

Pricing is explicitly configurable in `backend/config/dependency_simulation.yaml`. Defaults are zero so the application never presents an invented price. Enter approved current provider prices to enable cost estimation.

## Persistence

`dependency_simulation_operations` stores request, deterministic response, simulated state, narrative, AI metric reference and Temporal signalling evidence. `idempotencyKey` is unique.

## Safety

The Settings validator rejects production startup when any dependency mode is `SIMULATED`. Simulator API routes also reject production requests. Simulated identifiers are visibly non-production, such as:

```text
2SIM00000001
RGA-SIM-00000001
1ZSIM0000000001
IT@SIM00000001
```
