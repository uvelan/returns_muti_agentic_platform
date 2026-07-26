# Stage 4N — AI Gateway Routing, Safety, and Simulator Validation

## Objective

Harden the production Return Platform AI boundary without allowing AI availability or model behavior to control authoritative return operations.

Stage 4N adds:

- provider credential lists;
- lightweight and standard model lists;
- deterministic task-to-tier routing;
- model, credential, and provider failover;
- application, tier, provider, model, credential, and route limits;
- circuit breakers and bounded retries;
- prompt-injection and domain safeguards;
- exact output schemas;
- complete per-attempt metrics;
- deterministic fallback templates;
- dedicated AI operations pages;
- dependency-simulator AI validation.

## Architectural rule

```text
Typed AI task
→ deterministic input and domain policy
→ select the smallest capable model tier
→ select a healthy provider/model/key route
→ reserve quota and concurrency
→ bounded provider call
→ exact output validation
→ persist metrics
→ deterministic business layer decides the action
```

AI never creates or confirms RMA, RGA, tracking, booking, pickup, receipt, license plate, refund, product resolution, or vendor credit facts.

## Credential and model lists

Credentials are secret lists in `.env`:

```env
PLATFORM_GOOGLE_API_KEYS=["key-a","key-b"]
PLATFORM_NVIDIA_API_KEYS=["key-x","key-y"]
```

Models are independently configured by complexity:

```env
PLATFORM_GOOGLE_LIGHTWEIGHT_MODELS=["light-model-a","light-model-b"]
PLATFORM_GOOGLE_STANDARD_MODELS=["standard-model-a","standard-model-b"]
```

The gateway expands these into runtime routes such as:

```text
google/light-model-a/google-key-1
google/light-model-a/google-key-2
google/light-model-b/google-key-1
google/standard-model-a/google-key-1
```

Raw credentials are never returned by APIs or written to metrics. Only safe IDs such as `google-key-2` are persisted.

## Task complexity tiers

The task registry is `backend/config/ai_gateway.yaml`.

### Lightweight

- return eligibility;
- smart-question phrasing;
- return status summary;
- customer notification draft;
- dependency simulator narrative.

Simulator narratives are permanently lightweight-only and cannot escalate to standard models.

### Standard

- conflicting order candidate analysis;
- multi-message Support case analysis;
- cross-system reconciliation analysis;
- feedback recommendations.

The task ID—not an AI response—selects the tier.

## Routing and failure behavior

The route pool filters and ranks routes by:

1. task tier and provider allowlist;
2. configured credentials and models;
3. credential, model, provider, and route circuit state;
4. request and token limits;
5. concurrency;
6. provider, model, and credential priority;
7. active load and recent failures.

Error handling:

| Failure | Behavior |
|---|---|
| Credential authentication | Isolate the affected credential |
| Credential rate limit | Cool down the affected credential and use another key |
| Model unavailable/context limit | Isolate the model and use the next compatible model |
| Provider unavailable | Open provider circuit after threshold and use the next provider |
| Invalid JSON/schema | Reject the response and continue failover |
| Unsafe output | Reject the response and continue failover |
| All routes unavailable | Use the configured deterministic fallback |

Retries are globally bounded. There is no infinite retry path.

## Rate limiting

The in-process route pool enforces:

- application requests and tokens per minute;
- lightweight and standard tier budgets;
- provider budgets;
- model budgets;
- credential budgets;
- route budgets;
- tier and provider concurrency.

For multi-instance production deployment, replace or complement the in-process counters with Valkey atomic token buckets while preserving the same route contracts.

## Safety and domain boundary

The safety layer rejects:

- requests to ignore or replace system instructions;
- attempts to reveal hidden prompts or credentials;
- role impersonation and authorization bypass;
- requests to create authoritative return facts directly;
- direct SQL requests;
- encoded or obfuscated instruction attempts;
- general-purpose requests outside Ferguson return operations.

Untrusted customer and external-system text is treated as data. Production tasks use a fixed prompt registry and exact input allowlists. Custom prompts are restricted to development/test tooling.

When blocked, the deterministic response states that the assistant supports Ferguson return operations only.

## Exact outputs

Eligibility output permits exactly:

```json
{
  "decision": "APPROVE|REJECT|REVIEW_REQUIRED",
  "explanation": "bounded explanation",
  "confidenceMillionths": 0
}
```

Simulator narrative output permits exactly:

```json
{
  "message": "...",
  "summary": "...",
  "nextAction": "..."
}
```

Unknown fields, missing fields, invalid types, unsafe text, empty output, or invalid JSON are rejected.

## Metrics

Every attempt stores:

- trace and return session IDs;
- task and prompt versions;
- configured and selected tier;
- provider, model, credential safe ID, and route ID;
- attempt number and selection reason;
- status, failure, and fallback reason;
- latency and rate-limit wait;
- input, output, and total tokens;
- configured estimated cost;
- request and response digests;
- safety and schema-validation outcome.

Collections:

```text
ai_traces
ai_gateway_attempt_metrics
dependency_simulation_ai_metrics
```

## Dedicated pages

```text
/ai-gateway/requests
/ai-gateway/routes
/ai-gateway/tasks
/ai-gateway/metrics
/ai-gateway/safety
/ai-gateway/requests/:traceId
/system/dependency-simulator/ai-metrics
```

## Source validation

Stage 4N includes dependency-light tests using scripted provider routes. They prove:

- key list and model list expansion;
- lightweight/standard route isolation;
- credential rotation after rate limiting;
- model rotation after model unavailability;
- prompt-injection and out-of-domain blocking;
- successful lightweight simulator AI response;
- token/key/model/route metrics;
- deterministic fallback after provider failure;
- deterministic fallback after invalid output schema;
- successful RMA/label simulation even when AI fails.

These tests do not require live provider keys or paid network calls.
