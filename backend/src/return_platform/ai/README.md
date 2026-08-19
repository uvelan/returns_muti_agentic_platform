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
| `gateway/final_dispatch.py` | **`FinalDispatcher` — the one place a provider is called.** Owns the interception decision, the caller precondition, route selection, acquire/release, retry and deadline, recursive redaction, the single `provider.generate` expression, output safety, failover bookkeeping, pricing and attempt telemetry. |
| `gateway/service.py` | `AIGatewayService.evaluate` — the decision-shaped *response contract*: `{decision, explanation, confidenceMillionths}`, the persistent `AITrace`, the session quota, and a deterministic manual-review fallback. |
| `gateway/structured_invocation.py` | `StructuredOutputInvoker` — the structured-output *response contract*: any pydantic response model, parsed back from the same dispatch. |
| `gateway/models.py` | API-facing views for routes, tasks, usage, and safety tests. |

### One boundary, several response contracts

`AIGatewayService.evaluate`, `StructuredOutputInvoker.invoke` and the dependency
simulator's narrative service are **not** separate execution paths. Until AI-02 they were:
each owned a copy of the machinery around the call, and a control attached to one copy was
absent from the others — interception existed only on the decision path, recursive redaction
had to be remembered per loop and the simulator's never got it, and cost came from the
released catalog on two loops and from a local table returning `0` on the third.

They now differ only in response contract and in what they persist alongside. Everything
provider-facing arrives from `FinalDispatcher` via a `DispatchRequest`, a `validate`
callable and a `DispatchObserver`.

`evaluate` enforces a fixed decision envelope and answers with `REVIEW_REQUIRED` when every
route fails, because its callers are business decisions that must always produce an outcome.
`invoke` enforces an arbitrary caller-supplied pydantic schema and *raises* when every route
fails, because its callers are reasoning loops that must not be handed a fabricated result.

A caller needing structured output must not reach for `evaluate`: its parser accepts exactly
three keys and will reject anything else as `RESPONSE_INVALID`.

`tests/test_ai_single_dispatch_boundary.py` enforces this by enumerating every
`AIProvider.generate` call site in the source tree. Adding a fourth loop fails it.

### Interception

Every dispatch begins with one `InterceptionPolicy` verdict: `ALLOW_PROVIDER`,
`HUMAN_RESPONSE` or `REJECT` (contract C7). There is no default — a caller must state its
policy, because `= ALLOW_ALL` on a constructor is precisely what let the Order Agent and the
Graph Analyzer run ungated while the eligibility path was gated (AI-01). `ALLOW_ALL` remains
a legitimate answer for a process with no interception store, and `build_interception_policy`
logs `ai_interception_ungated` naming the path when it returns one.

`DurableInterceptionPolicy` holds rather than blocks. A first call persists the redacted
request to `ai/interception/store.py` as `PENDING` and reports `HUMAN_RESPONSE`; the operator
decides; `InterceptionResumeDispatcher` turns the decided record into a
`reasoning_resume_commands` row; the resume worker signals the run; the resumed dispatch finds
the decision. The interception id is *derived* from the task, the request digest and the
caller's correlation, so a retry re-finds its own interception instead of opening a second.

Five stored states map onto the three contract outcomes: `ALLOWED` → `ALLOW_PROVIDER`,
`PENDING`/`ANSWERED` → `HUMAN_RESPONSE`, `CANCELLED`/`EXPIRED` → `REJECT`. `ALLOWED` is the
one the platform did not model before AI-01; without it interception could only divert
traffic, never approve it.

**Redaction runs before the policy, not after.** Interception persists the held request for an
operator to read, so masking at `ProviderRequest` construction would seal customer data into a
store the provider itself never received. `dispatch` masks once, at the top.

`DurableInterceptionProvider` is **not** this mechanism. It is a MANUAL provider, gated to
development and test, that *replaces* the model with a human and reports `MANUAL` so a human
answer is never recorded as model output. Gating dispatch and substituting the thing
dispatched to are different things.

### Configuration freshness

`FinalDispatcher` takes no `configuration` argument. The active document is read from
`AIRoutePool` on every dispatch, because `replace_routes` swaps routes and configuration
together under the pool's own lock and is therefore the only object an activation updates
atomically. Callers resolve their task per invocation through `FinalDispatcher.task`, so
prompt version, tier, token ceilings, allowed providers, retry limits and pricing follow an
activated release the same way routes already did. Constructor copies of a task previously
pinned all of those for the life of a process while routes hot-applied around them.

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
  keys, or any resolved credential. Credentials appear in traces only as a
  `credential_fingerprint` — a truncated SHA-256, never the key.
- **A human response is never attributed to a model provider** — not in storage, not in
  metrics, not in the UI.
- **Untrusted source data is data, never instructions** (design §10.5). Prompts that carry
  source samples use the six-block framing: system policy, module policy, task, source
  metadata, untrusted sample, user requirements.

## There is one import path

`return_platform.ai_gateway` was a pure re-export layer, kept while ~30 modules outside this
lane still imported it. Those imports were rewritten to the canonical modules and the shim
was deleted, so `return_platform.ai` is the only way in.

`ai_gateway.routing` did not map onto one module: it re-exported `AIRoute`/`build_routes`
from `routing/routes.py` and the pool types from `routing/selection.py`. Callers import from
whichever of the two they actually need. `routing/__init__.py` stays free of re-exports for
the reason its own docstring gives — `tasks` is imported by both `routes` and `selection`,
so a package-level re-export would make importing `tasks` pull in the whole subpackage.

`tests/platform/test_ai_lane_boundary.py` fails if the old path is imported anywhere.

## Not built yet

`interception/`, `metrics/`, and `api/` are Phase 14 and later. `gateway/envelope.py`,
`gateway/resilience.py`, and `gateway/fallback.py` do not exist as separate modules: that
logic currently lives inside `routing/selection.py` and `gateway/service.py`, and empty
modules bearing those names would be placeholders, not architecture.
