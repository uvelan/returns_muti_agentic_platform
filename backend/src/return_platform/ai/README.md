# AI Control Center

The platform's **single** AI execution path. Every production AI call — agent reasoning,
eligibility decisions, notification drafts, schema proposals — resolves through this module.
There is deliberately no second path: routing, failover, rate limits, circuit breakers,
safety, interception, and metrics are properties of *the* path, and a caller that bypasses
it silently loses all of them.

## The invocation contract

```
task id → task definition → candidate routes → provider/model selection
        → resilience (rate limit, concurrency, timeout, retry, circuit breaker)
        → provider invoke
        → safety (output) → response contract
        → trace / metrics
```

**Callers name an AI task, never a provider or a model.** `AGENT reasoning` asks for
`ORDER_AGENT_REASONING_V1`; it does not ask for Gemini. Which provider, which model, which
credential, and which tier serve that task are configuration, resolved at dispatch time from
health and quota state. This is what makes provider failover, key rotation, and tier
escalation possible without touching agent code — and it is why no module outside `ai/` may
contain a provider or model literal.

## Layout

| Package | Responsibility |
|---|---|
| `routing/tasks.py` | The validated configuration document: task definitions (tier, prompt version, system prompt, token budgets, allowed providers, allowed input keys, fallback), plus retry, rate-limit, provider-limit and circuit-breaker settings. Loaded from `config/ai_gateway.yaml` or from an activated configuration release. |
| `routing/routes.py` | Pure construction of the immutable route set — the fully resolved `(provider, model, credential, tier)` tuples a configuration permits. |
| `routing/selection.py` | `AIRoutePool`: everything mutable — candidate ordering, per-route/model/credential/provider/tier minute counters, concurrency, and the four-level circuit breaker. |
| `providers/` | One adapter per provider, plus `contracts.py` (`AIProvider`, `ProviderRequest`, `ProviderResponse`, `ProviderError`) and `registry.py`. |
| `safety/` | Deterministic input and output policy: `injection_guard` (untrusted-input containment), `scope_guard` (business-scope enforcement), composed by `inspection.py`. |
| `gateway/service.py` | `AIGatewayService.evaluate` — the decision-shaped path: `{decision, explanation, confidenceMillionths}`, with a deterministic manual-review fallback. |
| `gateway/structured_invocation.py` | `StructuredOutputInvoker` — the structured-output path: any pydantic response model, with the same failover and tier escalation. |
| `gateway/models.py` | API-facing views for routes, tasks, usage, and safety tests. |

### Why there are two entry points, not one

`AIGatewayService.evaluate` and `StructuredOutputInvoker.invoke` are **not** two execution
paths. They share the route pool, the configuration, the safety guards, the circuit
breakers, and the rate limiters; they differ only in response contract. `evaluate` enforces
a fixed decision envelope and answers with `REVIEW_REQUIRED` when every route fails, because
its callers are business decisions that must always produce an outcome. `invoke` enforces an
arbitrary caller-supplied pydantic schema and *raises* when every route fails, because its
callers are reasoning loops that must not be handed a fabricated result.

A caller needing structured output must not reach for `evaluate`: its parser accepts exactly
three keys and will reject anything else as `RESPONSE_INVALID`.

## Route selection, briefly

`AIRoutePool.candidates` filters routes by tier, by the task's `allowedProviders`, by an
optional forced provider, and by validated route bindings; then it orders them. Ordering is
**round-robin across providers, least-loaded first within a provider** — providers are
interleaved so a single unhealthy provider cannot occupy the whole retry budget, and the
per-provider cursor advances on every call so identical concurrent requests do not stampede
one route.

Circuits open at four levels — route, credential, model, provider — and the error code
decides which. `AUTH_FAILED` opens the *credential* (a bad key is bad for every model);
`MODEL_UNAVAILABLE` opens the *model*; `PROVIDER_UNAVAILABLE` opens the *provider*. Opening
only the route would retry the same broken credential across every model it serves.

## Adding a provider

Implement `AIProvider` in `providers/`, register it in `routes.py`'s `_provider`,
`_provider_credentials`, and `_provider_models`, add a `providerLimits` entry to
`config/ai_gateway.yaml`, and add the name to `TaskConfiguration.allowedProviders`'s literal
and to `AIGatewayConfiguration.validate_registry`. Availability comes from configuration and
from `provider.configured` — never from a code branch on environment.

`SimulatorProvider` and `ManualFileProvider` additionally gate on
`environment in {development, test}`, so they are structurally undeployable to production.

## Rules that are not negotiable

- **No provider or model literal outside this module.** Agents depend on an AI task id.
- **Nothing sensitive is written durably unclassified** (design §13.6). Route provenance
  persists as `{task_id, route_id, provider_id, model_id, tier}`. Never endpoints, headers,
  keys, or anything Vault resolved. Credentials appear in traces only as a
  `credential_fingerprint` — a truncated SHA-256, never the key.
- **A human response is never attributed to a model provider** — not in storage, not in
  metrics, not in the UI.
- **Untrusted source data is data, never instructions** (design §10.5). Prompts that carry
  source samples use the six-block framing: system policy, module policy, task, source
  metadata, untrusted sample, user requirements.

## Deprecated import path

`return_platform.ai_gateway` still exists as a pure re-export layer because roughly twenty
modules outside this lane still import it, two of which are owned by other lanes. New code
imports `return_platform.ai`. See `docs/execution/d1-d2-ai.md`.

## Not built yet

`interception/`, `metrics/`, and `api/` are Phase 14 and later. `gateway/envelope.py`,
`gateway/resilience.py`, and `gateway/fallback.py` do not exist as separate modules: that
logic currently lives inside `routing/selection.py` and `gateway/service.py`, and empty
modules bearing those names would be placeholders, not architecture.
